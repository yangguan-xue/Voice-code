from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from voice_code.memory.candidate_service import CandidateDispositionService
from voice_code.memory.candidates import (
    CandidateStatus,
    ExtractionMode,
    MemoryAuthority,
    MemoryCandidate,
    SensitivityDecision,
)
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.repository import MemoryRepository


def _candidate(candidate_id="cand_0123456789abcdef") -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content="回答时先给结论。",
        confidence=0.96,
        evidence_text="以后回答先说结论",
        source_session_id="session-1",
        source_turn_id="turn-1",
        extraction_run_id="run_0123456789abcdef",
        extractor_model="extractor-model",
        sensitivity=SensitivityDecision.SAFE,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_shadow_mode_persists_candidate_but_never_active_memory(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    service = CandidateDispositionService(repository)

    result = service.process(
        _candidate(),
        mode=ExtractionMode.SHADOW,
        user_text="以后回答先说结论",
        related=[],
    )

    assert result.applied is True
    assert result.memory is None
    assert repository.list(user_id="local") == []
    stored = repository.list_candidates(user_id="local")[0]
    assert stored.status is CandidateStatus.REJECTED
    assert stored.reason_code == "SHADOW_ONLY"


def test_review_mode_requires_approval_without_creating_memory(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    service = CandidateDispositionService(repository)

    result = service.process(
        _candidate(),
        mode=ExtractionMode.REVIEW,
        user_text="以后回答先说结论",
        related=[],
    )

    assert result.memory is None
    stored = repository.list_candidates(user_id="local")[0]
    assert stored.status is CandidateStatus.CONFIRMATION_PENDING
    assert stored.reason_code == "REVIEW_MODE"


def test_automatic_create_is_transactional_and_idempotent(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    service = CandidateDispositionService(repository)
    candidate = _candidate()

    first = service.process(
        candidate,
        mode=ExtractionMode.AUTOMATIC,
        user_text="以后回答先说结论",
        related=[],
    )
    duplicate = service.process(
        candidate,
        mode=ExtractionMode.AUTOMATIC,
        user_text="以后回答先说结论",
        related=[],
    )

    assert first.applied is True
    assert first.memory is not None
    assert first.memory.authority is MemoryAuthority.AUTOMATIC_EXTRACTION
    assert duplicate.applied is False
    assert duplicate.memory == first.memory
    assert len(repository.list(user_id="local")) == 1
    with sqlite3.connect(repository.database_path) as connection:
        evidence_count = connection.execute(
            "SELECT count(*) FROM memory_evidence"
        ).fetchone()[0]
        mutation_count = connection.execute(
            "SELECT count(*) FROM memory_mutation_log"
        ).fetchone()[0]
        outbox_count = connection.execute(
            "SELECT count(*) FROM memory_index_outbox WHERE operation='upsert'"
        ).fetchone()[0]
    assert evidence_count == 1
    assert mutation_count == 1
    assert outbox_count == 1


def test_rejected_candidate_writes_audit_but_no_memory_or_outbox(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    service = CandidateDispositionService(repository)
    candidate = _candidate()

    result = service.process(
        candidate,
        mode=ExtractionMode.AUTOMATIC,
        user_text="没有候选证据",
        related=[],
    )

    assert result.memory is None
    assert result.decision.reason_code == "EVIDENCE_NOT_FOUND"
    with sqlite3.connect(repository.database_path) as connection:
        mutation_count = connection.execute(
            "SELECT count(*) FROM memory_mutation_log WHERE action='reject'"
        ).fetchone()[0]
        outbox_count = connection.execute(
            "SELECT count(*) FROM memory_index_outbox"
        ).fetchone()[0]
    assert mutation_count == 1
    assert outbox_count == 0
