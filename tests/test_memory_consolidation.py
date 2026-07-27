from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from voice_code.memory.candidate_service import CandidateDispositionService
from voice_code.memory.candidates import (
    ExtractionMode,
    MemoryCandidate,
    SensitivityDecision,
)
from voice_code.memory.consolidation import MemoryConsolidator
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.repository import MemoryRepository


def _automatic_memory(repository, candidate_id, content, evidence, turn_id):
    candidate = MemoryCandidate(
        candidate_id=candidate_id,
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content=content,
        confidence=0.97,
        evidence_text=evidence,
        source_session_id="session-1",
        source_turn_id=turn_id,
        extraction_run_id=f"run_{turn_id}",
        extractor_model="extractor-model",
        sensitivity=SensitivityDecision.SAFE,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = CandidateDispositionService(repository).process(
        candidate,
        mode=ExtractionMode.AUTOMATIC,
        user_text=evidence,
        related=[],
    )
    assert result.memory is not None
    return result.memory


def test_consolidation_dry_run_does_not_mutate_and_apply_preserves_evidence(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    first = _automatic_memory(
        repository,
        "cand_automatic_1",
        "回答时先给结论。",
        "回答时先给结论",
        "turn-1",
    )
    second = _automatic_memory(
        repository,
        "cand_automatic_2",
        "回答时要先给结论。",
        "回答时要先给结论",
        "turn-2",
    )
    consolidator = MemoryConsolidator(repository, similarity_threshold=0.85)

    proposals = consolidator.plan(user_id="local")

    assert len(proposals) == 1
    assert {proposals[0].keeper_id, proposals[0].duplicate_id} == {first.id, second.id}
    assert len(repository.list(user_id="local")) == 2

    applied = consolidator.apply(proposals, user_id="local")

    assert applied == 1
    remaining = repository.list(user_id="local")
    assert len(remaining) == 1
    assert remaining[0].version == 2
    with sqlite3.connect(repository.database_path) as connection:
        evidence_count = connection.execute(
            "SELECT count(*) FROM memory_evidence WHERE memory_id = ?",
            (remaining[0].id,),
        ).fetchone()[0]
        superseded_count = connection.execute(
            "SELECT count(*) FROM memory_entries WHERE status='superseded'"
        ).fetchone()[0]
    assert evidence_count == 2
    assert superseded_count == 1


def test_consolidation_never_proposes_explicit_memories(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.remember(
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content="回答时先给结论。",
        source_session_id="session-1",
    )
    _automatic_memory(
        repository,
        "cand_automatic_1",
        "回答时要先给结论。",
        "回答时要先给结论",
        "turn-2",
    )

    assert MemoryConsolidator(repository).plan(user_id="local") == []
