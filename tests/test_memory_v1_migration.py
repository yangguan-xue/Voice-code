from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from voice_code.memory.migration_v1 import migrate_v1
from voice_code.memory.models import (
    MemoryEntry,
    MemoryScope,
    MemorySource,
    MemoryType,
)
from voice_code.memory.rag_models import MemoryAuthority
from voice_code.memory.repository import MemoryRepository
from voice_code.memory.store import create_entry


def test_v1_migration_is_dry_run_safe_and_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("REASONING_HOME", str(tmp_path / "reasoning-home"))
    project_root = str(tmp_path / "project")
    timestamp = datetime(2026, 1, 2, tzinfo=UTC)
    legacy = MemoryEntry(
        id="mem_legacy_preference",
        name="回答风格",
        type=MemoryType.USER,
        scope=MemoryScope.USER,
        description="偏好简洁回答",
        content="偏好简洁回答，不需要重复总结。",
        created_at=timestamp,
        updated_at=timestamp,
        source=MemorySource(kind="explicit", session_id="legacy-session"),
    )
    create_entry(legacy, project_root)
    repository = MemoryRepository(tmp_path / "memory-v2.db")

    dry_run = migrate_v1(repository, project_root=project_root, dry_run=True)
    first = migrate_v1(repository, project_root=project_root, dry_run=False)
    second = migrate_v1(repository, project_root=project_root, dry_run=False)

    assert dry_run.discovered == 1
    assert dry_run.imported == 0
    migrated = repository.list(user_id="local")[0]
    assert migrated.id == legacy.id
    assert migrated.created_at == timestamp
    assert migrated.authority is MemoryAuthority.MIGRATED
    assert first.imported == 1
    assert second.imported == 0
    assert second.duplicates == 1
    with sqlite3.connect(repository.database_path) as connection:
        journal_count = connection.execute(
            "SELECT count(*) FROM memory_migration_journal"
        ).fetchone()[0]
    assert journal_count == 1
