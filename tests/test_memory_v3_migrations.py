from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from voice_code.memory.candidates import (
    CandidateStatus,
    MemoryCandidate,
    SensitivityDecision,
)
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.repository import (
    _MIGRATION_V1,
    _MIGRATION_V2,
    MemoryRepository,
)


def _create_v2_database(database_path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(_MIGRATION_V1)
        connection.executescript(_MIGRATION_V2)
        connection.execute(
            """
            INSERT INTO memory_entries(
                id, user_id, project_key, project_key_key, scope, kind, content,
                summary, status, source_session_id, content_hash, version,
                created_at, updated_at
            ) VALUES (
                'mem_existing', 'local', NULL, '', 'user', 'preference',
                '回答尽量简洁。', '回答尽量简洁。', 'active', 'session-old',
                'hash-existing', 4, '2026-01-01T00:00:00+00:00',
                '2026-01-02T00:00:00+00:00'
            )
            """
        )
        connection.execute("PRAGMA user_version=2")


def _candidate() -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id="cand_0123456789abcdef",
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


def test_v2_database_migrates_to_v3_without_changing_existing_memory(tmp_path):
    database_path = tmp_path / "memory.db"
    _create_v2_database(database_path)

    repository = MemoryRepository(database_path)
    existing = repository.get("mem_existing", user_id="local")

    assert existing is not None
    assert existing.content == "回答尽量简洁。"
    assert existing.version == 4
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memory_entries)")
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"authority", "confidence", "review_state", "recall_count"} <= columns
    assert {
        "memory_candidates",
        "memory_evidence",
        "memory_mutation_log",
        "memory_extraction_runs",
        "memory_extraction_jobs",
        "memory_dead_letters",
    } <= tables


def test_candidate_persistence_is_idempotent_by_turn_and_content(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    candidate = _candidate()

    first = repository.save_candidate(candidate, status=CandidateStatus.PENDING)
    duplicate = repository.save_candidate(candidate, status=CandidateStatus.PENDING)
    stored = repository.list_candidates(
        user_id="local",
        status=CandidateStatus.PENDING,
    )

    assert first is True
    assert duplicate is False
    assert len(stored) == 1
    assert stored[0].candidate == candidate
    assert stored[0].status is CandidateStatus.PENDING
