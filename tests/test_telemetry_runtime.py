from __future__ import annotations

import io
import json
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import tool

from voice_code.agent.loop import agent_loop
from voice_code.agent.sinks import TranscriptSink
from voice_code.permissions import PermissionContext
from voice_code.session import resume as session_resume
from voice_code.session import state as session_state
from voice_code.session.resume import resume_runtime_session
from voice_code.session.state import (
    build_session_runtime_state,
    load_session_state,
    save_session_state,
)
from voice_code.session.transcript import TranscriptWriter
from voice_code.telemetry import configure_logging
from voice_code.telemetry.instrumentation import (
    configure_telemetry_backend,
    reset_telemetry_for_tests,
)
from voice_code.telemetry.otel import OpenTelemetryBackend


class _CapturingBackend:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, str]]] = []
        self.counter_values: list[tuple[str, float]] = []
        self.histograms: list[tuple[str, dict[str, str]]] = []
        self.spans: list[str] = []

    def add_counter(self, name: str, value: float, attributes: dict[str, str]) -> None:
        self.counters.append((name, attributes))
        self.counter_values.append((name, value))

    def record_histogram(
        self, name: str, value: float, attributes: dict[str, str]
    ) -> None:
        self.histograms.append((name, attributes))

    @contextmanager
    def start_span(self, name: str, attributes: dict[str, str]):
        self.spans.append(name)
        yield


@pytest.fixture(autouse=True)
def _reset_instrumentation() -> None:
    reset_telemetry_for_tests()
    yield
    reset_telemetry_for_tests()


def _json_events(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_agent_tool_and_permission_events_share_one_correlation_context() -> None:
    stream = io.StringIO()
    configure_logging(json_output=True, stream=stream, force=True)
    backend = _CapturingBackend()
    configure_telemetry_backend(backend)

    @tool
    def echo_tool(value: str) -> str:
        """Echo a value."""
        return f"private tool output: {value}"

    echo_tool.metadata = {"is_readonly": False}
    groups = [
        [
            AIMessageChunk(content="using tool"),
            AIMessageChunk(
                content="",
                tool_calls=[
                    {
                        "name": "echo_tool",
                        "args": {"value": "private tool argument"},
                        "id": "tool-call-1",
                    }
                ],
            ),
        ],
        [AIMessageChunk(content="done")],
    ]
    call_index = 0

    async def fake_astream(*_args: object, **_kwargs: object) -> AsyncGenerator:
        nonlocal call_index
        for chunk in groups[call_index]:
            yield chunk
        call_index += 1

    model = MagicMock()
    model.model_name = "test-model"
    model.astream = fake_astream

    events = []
    async for event in agent_loop(
        user_input="private user input",
        tools=[echo_tool],
        system_prompt="private system prompt",
        model=model,
        permission_context=PermissionContext(mode="bypassPermissions"),
        runtime_session_id="session-telemetry",
    ):
        events.append(event)

    assert events[-1].finish_reason == "completed"
    payloads = _json_events(stream)
    names = [payload["event"] for payload in payloads]
    assert names == [
        "agent.turn.started",
        "llm.request.started",
        "llm.request.finished",
        "permission.decided",
        "tool.execution.started",
        "tool.execution.finished",
        "llm.request.started",
        "llm.request.finished",
        "agent.turn.finished",
    ]
    assert {payload["correlation_id"] for payload in payloads} == {
        payloads[0]["correlation_id"]
    }
    assert {payload["session_id"] for payload in payloads} == {"session-telemetry"}
    assert {payload["turn_id"] for payload in payloads} == {payloads[0]["turn_id"]}
    tool_payloads = [payload for payload in payloads if str(payload["event"]).startswith("tool.")]
    assert {payload["tool_call_id"] for payload in tool_payloads} == {"tool-call-1"}

    serialized = stream.getvalue()
    assert "private user input" not in serialized
    assert "private system prompt" not in serialized
    assert "private tool argument" not in serialized
    assert "private tool output" not in serialized
    assert [name for name, _ in backend.counters] == [
        "llm_requests_total",
        "permission_decisions_total",
        "tool_calls_total",
        "llm_requests_total",
        "agent_turns_total",
    ]
    assert [name for name, _ in backend.histograms] == [
        "llm_request_duration_seconds",
        "tool_duration_seconds",
        "llm_request_duration_seconds",
        "agent_turn_duration_seconds",
    ]
    assert backend.spans == [
        "agent.turn",
        "llm.request",
        "permission.evaluate",
        "tool.execute",
        "llm.request",
    ]


@pytest.mark.asyncio
async def test_provider_timeout_uses_stable_error_code_without_exception_text() -> None:
    stream = io.StringIO()
    configure_logging(json_output=True, stream=stream, force=True)
    backend = _CapturingBackend()
    configure_telemetry_backend(backend)

    async def timeout_stream(*_args: object, **_kwargs: object) -> AsyncGenerator:
        raise TimeoutError("Authorization: Bearer private-provider-token")
        yield

    model = MagicMock()
    model.model_name = "test-model"
    model.astream = timeout_stream

    agent_events = []
    async for agent_event in agent_loop(
        user_input="hello",
        tools=[],
        system_prompt="system",
        model=model,
        runtime_session_id="session-timeout",
    ):
        agent_events.append(agent_event)

    payloads = _json_events(stream)
    failure = next(payload for payload in payloads if payload["event"] == "llm.request.failed")
    assert failure["error_code"] == "PROVIDER_TIMEOUT"
    assert failure["exception_type"] == "TimeoutError"
    assert "private-provider-token" not in stream.getvalue()
    assert agent_events[0].error_code == "PROVIDER_TIMEOUT"
    assert "private-provider-token" not in agent_events[0].content
    assert ("llm_requests_total", {
        "model": "test-model",
        "outcome": "timeout",
        "provider": "magicmock",
    }) in backend.counters
    assert ("agent_turns_total", {"mode": "default", "outcome": "error"}) in backend.counters


@pytest.mark.asyncio
async def test_provider_failure_exposes_only_a_stable_error_code() -> None:
    async def failed_stream(*_args: object, **_kwargs: object) -> AsyncGenerator:
        raise RuntimeError("private provider failure with internal details")
        yield

    model = MagicMock()
    model.model_name = "test-model"
    model.astream = failed_stream

    events = []
    async for event in agent_loop(
        user_input="hello",
        tools=[],
        system_prompt="system",
        model=model,
        runtime_session_id="session-failure",
    ):
        events.append(event)

    assert events[0].error_code == "PROVIDER_UNAVAILABLE"
    assert events[0].content == "Model provider is unavailable. Please retry."
    assert "internal details" not in events[0].content


@pytest.mark.asyncio
async def test_tool_failure_exposes_only_a_stable_error_code() -> None:
    @tool
    def failed_tool() -> str:
        """Fail without exposing internal details."""
        raise RuntimeError("private tool failure with filesystem details")

    failed_tool.metadata = {"is_readonly": True}
    groups = [
        [
            AIMessageChunk(
                content="",
                tool_calls=[{"name": "failed_tool", "args": {}, "id": "failed-call"}],
            )
        ],
        [AIMessageChunk(content="handled")],
    ]
    call_index = 0

    async def fake_astream(*_args: object, **_kwargs: object) -> AsyncGenerator:
        nonlocal call_index
        for chunk in groups[call_index]:
            yield chunk
        call_index += 1

    model = MagicMock()
    model.model_name = "test-model"
    model.astream = fake_astream

    events = []
    async for event in agent_loop(
        user_input="run tool",
        tools=[failed_tool],
        system_prompt="system",
        model=model,
        runtime_session_id="session-tool-failure",
    ):
        events.append(event)

    failure = next(
        event
        for event in events
        if event.tool_call_id == "failed-call" and event.error_code
    )
    assert failure.error_code == "TOOL_EXECUTION_FAILED"
    assert "private tool failure" not in failure.content


def test_session_state_events_report_outcome_without_paths(monkeypatch, tmp_path) -> None:
    stream = io.StringIO()
    configure_logging(json_output=True, stream=stream, force=True)
    backend = _CapturingBackend()
    configure_telemetry_backend(backend)
    state_path = tmp_path / "private-user-directory" / "session.state.json"
    monkeypatch.setattr(session_state, "get_session_state_path", lambda _session_id: state_path)

    state = build_session_runtime_state(
        session_id="session-state",
        cwd="/private/user/workspace",
    )
    save_session_state(state)
    loaded = load_session_state("session-state")

    assert loaded.source == "loaded"
    payloads = _json_events(stream)
    assert [payload["event"] for payload in payloads] == [
        "session.state.saved",
        "session.state.loaded",
    ]
    assert {payload["session_id"] for payload in payloads} == {"session-state"}
    assert "/private/user/workspace" not in stream.getvalue()
    assert "private-user-directory" not in stream.getvalue()
    assert backend.counters == [
        ("sessions_total", {"operation": "save", "outcome": "success"}),
        ("sessions_total", {"operation": "load", "outcome": "loaded"}),
    ]


def test_invalid_session_state_uses_stable_error_code(monkeypatch, tmp_path) -> None:
    stream = io.StringIO()
    configure_logging(json_output=True, stream=stream, force=True)
    state_path = tmp_path / "session.state.json"
    state_path.write_text("not-json Authorization: Bearer private-token", encoding="utf-8")
    monkeypatch.setattr(session_state, "get_session_state_path", lambda _session_id: state_path)

    result = load_session_state("corrupt-session")

    assert result.source == "invalid"
    payload = _json_events(stream)[0]
    assert payload["event"] == "session.state.loaded"
    assert payload["outcome"] == "invalid"
    assert payload["error_code"] == "SESSION_STATE_CORRUPT"
    assert "private-token" not in stream.getvalue()


def test_transcript_persistence_creates_content_free_spans(tmp_path) -> None:
    backend = _CapturingBackend()
    configure_telemetry_backend(backend)
    writer = TranscriptWriter(tmp_path / "session.jsonl")

    TranscriptSink(writer).write(AIMessage(content="private response"))
    writer.close()

    assert backend.spans == ["transcript.persist"]


def test_session_resume_records_duration_metric_and_parent_span(monkeypatch, tmp_path) -> None:
    backend = _CapturingBackend()
    configure_telemetry_backend(backend)
    path = tmp_path / "session.jsonl"
    writer = TranscriptWriter(path, session_meta={"cwd": str(tmp_path)})
    writer.write_message(AIMessage(content="saved response"))
    writer.close()
    monkeypatch.setattr(session_resume, "get_session_path", lambda _session_id: path)
    monkeypatch.setattr(
        session_state,
        "get_session_state_path",
        lambda _session_id: tmp_path / "missing",
    )

    result = resume_runtime_session("session-resume")
    result.transcript_writer.close()

    assert ("session_resume_duration_seconds", {}) in backend.histograms
    assert backend.spans[0] == "session.resume"


@pytest.mark.asyncio
async def test_real_trace_connects_agent_llm_and_transcript_without_content(tmp_path) -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    configure_logging(json_output=True, stream=io.StringIO(), force=True)
    meter_provider = MeterProvider(shutdown_on_exit=False)
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(shutdown_on_exit=False)
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    configure_telemetry_backend(
        OpenTelemetryBackend(
            meter_provider.get_meter("voice-code"),
            tracer_provider.get_tracer("voice-code"),
        )
    )
    writer = TranscriptWriter(tmp_path / "trace-session.jsonl")

    async def fake_astream(*_args: object, **_kwargs: object) -> AsyncGenerator:
        yield AIMessageChunk(content="private response")

    model = MagicMock()
    model.model_name = "test-model"
    model.astream = fake_astream
    async for _ in agent_loop(
        user_input="private input",
        tools=[],
        system_prompt="private system prompt",
        model=model,
        transcript_writer=writer,
        runtime_session_id="session-trace",
    ):
        pass
    writer.close()

    spans = span_exporter.get_finished_spans()
    agent_span = next(span for span in spans if span.name == "agent.turn")
    llm_span = next(span for span in spans if span.name == "llm.request")
    nested_transcript = next(
        span
        for span in spans
        if span.name == "transcript.persist"
        and span.parent is not None
        and span.parent.span_id == agent_span.context.span_id
    )
    assert llm_span.parent is not None
    assert llm_span.parent.span_id == agent_span.context.span_id
    assert nested_transcript.attributes["operation"] == "write"
    attributes = repr([span.attributes for span in spans])
    assert "private input" not in attributes
    assert "private response" not in attributes
    assert "private system prompt" not in attributes


@pytest.mark.asyncio
async def test_llm_usage_metadata_records_input_and_output_tokens() -> None:
    configure_logging(json_output=True, stream=io.StringIO(), force=True)
    backend = _CapturingBackend()
    configure_telemetry_backend(backend)

    async def fake_astream(*_args: object, **_kwargs: object) -> AsyncGenerator:
        yield AIMessageChunk(
            content="done",
            usage_metadata={"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
        )

    model = MagicMock()
    model.model_name = "test-model"
    model.astream = fake_astream
    async for _ in agent_loop(
        user_input="hello",
        tools=[],
        system_prompt="system",
        model=model,
        runtime_session_id="session-tokens",
    ):
        pass

    token_counters = [item for item in backend.counters if item[0] == "llm_tokens_total"]
    assert token_counters == [
        (
            "llm_tokens_total",
            {"direction": "input", "model": "test-model", "provider": "magicmock"},
        ),
        (
            "llm_tokens_total",
            {"direction": "output", "model": "test-model", "provider": "magicmock"},
        ),
    ]
    assert [item for item in backend.counter_values if item[0] == "llm_tokens_total"] == [
        ("llm_tokens_total", 12),
        ("llm_tokens_total", 5),
    ]
