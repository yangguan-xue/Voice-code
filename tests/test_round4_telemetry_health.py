from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from voice_code.agent.types import AgentEvent, EventType
from voice_code.goals import GoalLoop, GoalSpec
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.rag_service import MemoryRagService
from voice_code.memory.repository import MemoryRepository
from voice_code.memory.vector_index import MemoryIndexUnavailableError
from voice_code.permissions import PermissionContext
from voice_code.status import build_health_status
from voice_code.status import main as status_main
from voice_code.subagents.registry import TaskRegistry
from voice_code.subagents.runtime import SubagentRuntime, SubagentRuntimeRequest
from voice_code.telemetry.context import bind_telemetry_context
from voice_code.telemetry.instrumentation import (
    configure_telemetry_backend,
    reset_telemetry_for_tests,
)
from voice_code.voice.orchestrator import VoiceOrchestrator


class CapturingBackend:
    def __init__(self) -> None:
        self.counters: list[tuple[str, float, dict[str, str]]] = []
        self.histograms: list[tuple[str, float, dict[str, str]]] = []
        self.spans: list[tuple[str, dict[str, str]]] = []

    def add_counter(self, name: str, value: float, attributes: dict[str, str]) -> None:
        self.counters.append((name, value, attributes))

    def record_histogram(self, name: str, value: float, attributes: dict[str, str]) -> None:
        self.histograms.append((name, value, attributes))

    @contextmanager
    def start_span(self, name: str, attributes: dict[str, str]):
        self.spans.append((name, attributes))
        yield


@pytest.fixture(autouse=True)
def _reset_backend() -> None:
    reset_telemetry_for_tests()
    yield
    reset_telemetry_for_tests()


class _FailingStt:
    async def transcribe_audio(self, _audio: bytes) -> str:
        raise RuntimeError("stt unavailable with secret-token")


class _Tts:
    def synthesize_stream(self, _text: str, **_: object):
        async def stream():
            yield b"audio", 16000

        return stream()

    async def synthesize_text(self, _text: str, **_: object) -> bytes:
        return b"RIFF"


class _Bridge:
    def on_event(self, _callback) -> None:
        return None

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def run_turn(self, _text: str) -> str:
        return "ok"

    async def summarize_for_speech(self, text: str) -> str:
        return text

    def interrupt(self) -> None:
        return None


class _Recorder:
    def on_segment(self, _callback) -> None:
        return None

    def on_raw_frame(self, _callback) -> None:
        return None

    def open_mic(self) -> None:
        return None

    def close_mic(self) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def drain_queue(self) -> None:
        return None


class _Player:
    async def play_chunk(self, _chunk: bytes, _sample_rate: int) -> None:
        return None

    async def close_stream(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def play_wav_bytes(self, _audio: bytes) -> None:
        return None


@pytest.mark.asyncio
async def test_voice_stt_failure_records_bounded_stage_telemetry() -> None:
    backend = CapturingBackend()
    configure_telemetry_backend(backend)
    orchestrator = VoiceOrchestrator(
        stt_client=cast(Any, _FailingStt()),
        tts_client=cast(Any, _Tts()),
        agent_bridge=cast(Any, _Bridge()),
        classifier=cast(Any, SimpleNamespace(classify=lambda _text: None)),
        segment_recorder=cast(Any, _Recorder()),
        audio_player=cast(Any, _Player()),
        console_output=False,
    )
    orchestrator._loop = __import__("asyncio").get_running_loop()
    await orchestrator._enter_listening()

    await orchestrator.handle_speech_segment(b"RIFF" + b"\0" * 128)

    assert ("voice_stage_total", 1.0, {"outcome": "error", "stage": "stt"}) in backend.counters
    assert any(item[0] == "voice_stage_duration_seconds" for item in backend.histograms)
    telemetry = backend.counters + backend.histograms + backend.spans
    assert all("secret-token" not in str(item) for item in telemetry)


@pytest.mark.asyncio
async def test_subagent_failure_records_lifecycle_latency_and_correlation(tmp_path) -> None:
    backend = CapturingBackend()
    configure_telemetry_backend(backend)

    async def failing_loop(**_: Any):
        yield AgentEvent(type=EventType.ERROR, turn=1, content="private failure", status="error")
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="error")

    runtime = SubagentRuntime(
        registry=TaskRegistry(),
        transcript_root=tmp_path,
        agent_loop_fn=failing_loop,
    )
    with bind_telemetry_context(correlation_id="corr-subagent"):
        await runtime.run(
            SubagentRuntimeRequest(
                task_id="task-secret",
                session_id="session-secret",
                parent_session_id=None,
                parent_task_id=None,
                agent_type="researcher",
                description="private",
                prompt="secret prompt",
                system_prompt="system",
                tools=[],
                model=object(),
                fallback_model=None,
                permission_context=PermissionContext(),
            )
        )

    assert (
        "subagent_tasks_total",
        1.0,
        {"agent_type": "researcher", "outcome": "error"},
    ) in backend.counters
    assert any(item[0] == "subagent_task_duration_seconds" for item in backend.histograms)
    assert (
        "subagent.run",
        {"correlation_id": "corr-subagent", "agent_type": "researcher"},
    ) in backend.spans
    telemetry = backend.counters + backend.histograms + backend.spans
    assert "secret prompt" not in str(telemetry)


@pytest.mark.asyncio
async def test_goal_builder_failure_records_iteration_telemetry(tmp_path) -> None:
    backend = CapturingBackend()
    configure_telemetry_backend(backend)
    spec = GoalSpec(
        "goal-one",
        "private objective",
        str(tmp_path),
        ["true"],
        require_review=False,
    )

    async def builder(_prompt: str) -> str:
        raise RuntimeError("provider failed with secret")

    with pytest.raises(RuntimeError):
        await GoalLoop(spec).run(builder)

    assert (
        "goal_iterations_total",
        1.0,
        {"outcome": "error", "stage": "build"},
    ) in backend.counters
    assert any(item[0] == "goal_iteration_duration_seconds" for item in backend.histograms)
    telemetry = backend.counters + backend.histograms + backend.spans
    assert "private objective" not in str(telemetry)


class _Embedder:
    async def embed_query(self, _text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FailingIndex:
    async def ensure_collection(self) -> None:
        raise MemoryIndexUnavailableError("milvus secret")

    async def hybrid_search(self, **_kwargs) -> list[object]:
        raise MemoryIndexUnavailableError("milvus secret")

    async def health(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_rag_fallback_and_outbox_failure_record_bounded_telemetry(tmp_path) -> None:
    backend = CapturingBackend()
    configure_telemetry_backend(backend)
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.remember(
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content="private memory text",
        source_session_id="session-secret",
    )
    service = MemoryRagService(
        repository,
        embedder=_Embedder(),
        vector_index=cast(Any, _FailingIndex()),
    )

    result = await service.retrieve("private query", user_id="local", project_key=None, limit=5)
    delivered = await service.sync_pending(limit=10)

    assert result.degraded is True
    assert delivered == 0
    assert (
        "rag_retrievals_total",
        1.0,
        {"backend": "sqlite_fts", "outcome": "degraded"},
    ) in backend.counters
    assert ("rag_outbox_total", 1.0, {"backend": "milvus", "outcome": "error"}) in backend.counters
    telemetry = backend.counters + backend.histograms + backend.spans
    assert "private memory text" not in str(telemetry)


@pytest.mark.asyncio
async def test_unified_health_json_schema_degrades_when_milvus_fails(tmp_path) -> None:
    repository = MemoryRepository(tmp_path / "memory.db")
    health = await build_health_status(
        repository=repository,
        vector_index=cast(Any, _FailingIndex()),
        transcript_dir=tmp_path,
        llm_provider="test",
        llm_healthy=True,
        voice_provider="disabled",
        voice_healthy=True,
        active_subagents=1,
        active_goals=0,
        background_tasks=0,
    )

    payload = health.to_dict()

    assert payload["status"] == "degraded"
    assert payload["components"]["sqlite"]["status"] == "healthy"
    assert payload["components"]["milvus"]["status"] == "degraded"
    assert set(payload) == {
        "schema_version",
        "status",
        "generated_at",
        "version",
        "runtime",
        "components",
    }
    assert "secret" not in str(payload).lower()


def test_status_json_cli_schema_omits_secrets(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("REASONING_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("REASONING_TRANSCRIPT_DIR", str(tmp_path / "transcripts"))
    monkeypatch.setenv("STEPFUN_API_KEY", "secret-value")

    status_main(["--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "1"
    assert payload["status"] in {"healthy", "degraded", "unhealthy"}
    assert payload["components"]["sqlite"]["status"] == "healthy"
    assert payload["components"]["milvus"]["status"] == "healthy"
    assert payload["components"]["milvus"]["enabled"] is False
    assert set(payload) == {
        "schema_version",
        "status",
        "generated_at",
        "version",
        "runtime",
        "components",
    }
    assert "secret-value" not in json.dumps(payload)
