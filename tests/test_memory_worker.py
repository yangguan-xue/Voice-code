from __future__ import annotations

import sqlite3

import pytest

from voice_code.memory.candidates import (
    CandidateStatus,
    ExtractionMode,
    SensitivityDecision,
)
from voice_code.memory.extraction import ExtractedCandidate
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.repository import MemoryRepository
from voice_code.memory.worker import MemoryExtractionWorker


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, _turn):
        self.calls += 1
        return [
            ExtractedCandidate(
                kind=MemoryKind.PREFERENCE,
                scope=MemoryScope.USER,
                content="回答时先给结论。",
                confidence=0.96,
                evidence_text="以后回答先给结论",
                sensitivity=SensitivityDecision.SAFE,
            )
        ]


def _enqueue(repository: MemoryRepository, *, user_text="以后回答先给结论") -> bool:
    return repository.enqueue_extraction_job(
        user_id="local",
        project_key="project-a",
        source_session_id="session-1",
        source_turn_id="turn-1",
        user_input=user_text,
        assistant_response="明白，我会先给结论。",
        mode=ExtractionMode.SHADOW,
    )


@pytest.mark.asyncio
async def test_worker_survives_restart_and_persists_shadow_run(tmp_path):
    database_path = tmp_path / "memory.db"
    first_repository = MemoryRepository(database_path)
    assert _enqueue(first_repository) is True
    assert _enqueue(first_repository) is False

    restarted_repository = MemoryRepository(database_path)
    provider = FakeProvider()
    worker = MemoryExtractionWorker(restarted_repository, provider)

    completed = await worker.run_once(limit=10)

    assert completed == 1
    assert provider.calls == 1
    stored = restarted_repository.list_candidates(user_id="local")
    assert stored[0].status is CandidateStatus.REJECTED
    assert stored[0].reason_code == "SHADOW_ONLY"
    assert restarted_repository.list(user_id="local") == []
    with sqlite3.connect(database_path) as connection:
        job_status = connection.execute(
            "SELECT status FROM memory_extraction_jobs"
        ).fetchone()[0]
        run = connection.execute(
            "SELECT status, candidate_count, accepted_count FROM memory_extraction_runs"
        ).fetchone()
    assert job_status == "completed"
    assert run == ("completed", 1, 0)


def test_extraction_job_lease_expires_and_attempt_is_recoverable(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    _enqueue(repository)

    first = repository.claim_extraction_jobs(limit=1, lease_seconds=30)
    unavailable = repository.claim_extraction_jobs(limit=1, lease_seconds=30)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE memory_extraction_jobs SET leased_until='2000-01-01T00:00:00+00:00'"
        )
    recovered = repository.claim_extraction_jobs(limit=1, lease_seconds=30)

    assert first[0].attempts == 1
    assert unavailable == []
    assert recovered[0].attempts == 2


def test_sensitive_turn_is_rejected_before_job_persistence(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")

    with pytest.raises(ValueError, match="sensitive"):
        _enqueue(repository, user_text="api_key=top-secret-value")

    with sqlite3.connect(repository.database_path) as connection:
        count = connection.execute("SELECT count(*) FROM memory_extraction_jobs").fetchone()[0]
    assert count == 0
