from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

from voice_code.memory.candidates import ExtractionMode
from voice_code.memory.extraction import ExtractionUnavailableError
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.rag_service import MemoryRagService
from voice_code.memory.repository import MemoryRepository
from voice_code.memory.worker import MemoryExtractionWorker
from voice_code.status import build_health_status


class AlwaysUnavailableProvider:
    async def extract(self, _turn):
        raise ExtractionUnavailableError("provider unavailable secret-token user text")


def _enqueue(repository: MemoryRepository) -> None:
    assert repository.enqueue_extraction_job(
        user_id="local",
        project_key=None,
        source_session_id="session-1",
        source_turn_id="turn-1",
        user_input="以后回答先给结论",
        assistant_response="明白",
        mode=ExtractionMode.AUTOMATIC,
    )


@pytest.mark.asyncio
async def test_real_process_exit_then_restart_recovers_extraction_backlog(tmp_path):
    database_path = tmp_path / "memory.db"
    enqueue_code = f"""
import os
from pathlib import Path
from voice_code.memory.candidates import ExtractionMode
from voice_code.memory.repository import MemoryRepository
repository = MemoryRepository(Path({str(database_path)!r}))
repository.enqueue_extraction_job(
    user_id='local',
    project_key=None,
    source_session_id='session-1',
    source_turn_id='turn-1',
    user_input='以后回答先给结论',
    assistant_response='明白',
    mode=ExtractionMode.AUTOMATIC,
)
os._exit(23)
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    crashed = subprocess.run([sys.executable, "-c", enqueue_code], env=env, check=False)
    assert crashed.returncode == 23

    restart_code = f"""
import asyncio
import json
from pathlib import Path
from voice_code.memory.candidates import SensitivityDecision
from voice_code.memory.extraction import ExtractedCandidate
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.repository import MemoryRepository
from voice_code.memory.worker import MemoryExtractionWorker
class Provider:
    async def extract(self, _turn):
        return [
            ExtractedCandidate(
                kind=MemoryKind.PREFERENCE,
                scope=MemoryScope.USER,
                content='回答先给结论。',
                confidence=0.97,
                evidence_text='以后回答先给结论',
                sensitivity=SensitivityDecision.SAFE,
            )
        ]
async def main():
    repository = MemoryRepository(Path({str(database_path)!r}))
    completed = await MemoryExtractionWorker(repository, Provider()).run_once(limit=10)
    print(
        json.dumps(
            {{'completed': completed, 'memories': len(repository.list(user_id='local'))}},
            ensure_ascii=False,
        )
    )
asyncio.run(main())
"""
    restarted = subprocess.run(
        [sys.executable, "-c", restart_code],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(restarted.stdout)
    assert result == {"completed": 1, "memories": 1}


@pytest.mark.asyncio
async def test_extraction_provider_long_failure_dead_letters_after_bounded_backoff(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    _enqueue(repository)
    worker = MemoryExtractionWorker(repository, AlwaysUnavailableProvider(), max_attempts=2)

    assert await worker.run_once(limit=1) == 0
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE memory_extraction_jobs SET available_at='2000-01-01T00:00:00+00:00'"
        )
    assert await worker.run_once(limit=1) == 0

    dead_letters = repository.list_dead_letters()
    assert len(dead_letters) == 1
    assert dead_letters[0]["source_type"] == "extraction"
    assert dead_letters[0]["attempts"] == 2
    assert dead_letters[0]["error_code"] == "ExtractionUnavailableError"
    assert "以后回答" not in json.dumps(dead_letters, ensure_ascii=False)
    with sqlite3.connect(repository.database_path) as connection:
        status = connection.execute("SELECT status FROM memory_extraction_jobs").fetchone()[0]
    assert status == "failed"


@pytest.mark.asyncio
async def test_outbox_dead_letter_is_listable_explainable_and_replayable(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    service = MemoryRagService(
        repository,
        embedder=object(),
        vector_index=object(),
        max_index_attempts=1,
    )
    memory = service.remember(
        "回答尽量简洁。",
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        session_id="session-1",
    ).entry

    class FailingEmbedder:
        async def embed_documents(self, _texts):
            raise TimeoutError("secret-token user content")

    class Index:
        async def ensure_collection(self):
            return None

    failing = MemoryRagService(
        repository,
        embedder=FailingEmbedder(),
        vector_index=Index(),
        max_index_attempts=1,
    )
    assert await failing.sync_pending(limit=10) == 0

    dead_letters = repository.list_dead_letters()
    assert len(dead_letters) == 1
    letter_id = dead_letters[0]["dead_letter_id"]
    explanation = repository.explain_dead_letter(letter_id)
    assert explanation["source_type"] == "outbox"
    assert explanation["source_id"]
    assert explanation["memory_id"] == memory.id
    assert "回答尽量简洁" not in json.dumps(explanation, ensure_ascii=False)

    assert repository.replay_dead_letter(letter_id) is True
    assert repository.explain_dead_letter(letter_id)["replayed_at"] is not None
    pending = repository.claim_outbox(limit=1, lease_seconds=30)
    assert pending[0].memory_id == memory.id
    assert pending[0].attempts == 1


@pytest.mark.asyncio
async def test_status_json_exposes_dlq_and_subagent_unrecoverable_semantics(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    _enqueue(repository)
    await MemoryExtractionWorker(repository, AlwaysUnavailableProvider(), max_attempts=1).run_once(
        limit=1
    )

    health = await build_health_status(
        repository=repository,
        vector_index=None,
        transcript_dir=tmp_path / "transcripts",
    )
    rag = health.to_dict()["components"]["rag_backlog"]
    runtime = health.to_dict()["components"]["runtime_tasks"]

    assert rag["dead_letter_count"] == 1
    assert runtime["subagent_recovery"] == "unrecoverable"
    assert runtime["subagent_recovery_path"] is None
