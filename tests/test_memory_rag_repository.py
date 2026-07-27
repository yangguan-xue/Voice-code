from __future__ import annotations

import sqlite3

from voice_code.memory.rag_models import IndexOperation, MemoryKind, MemoryScope
from voice_code.memory.repository import _MIGRATION_V1, MemoryRepository


def test_remember_is_persistent_and_idempotent(tmp_path):
    database = tmp_path / "memory.db"
    repository = MemoryRepository(database)

    first = repository.remember(
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content="以后回答尽量简洁。",
        source_session_id="session-1",
    )
    duplicate = repository.remember(
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content="  以后回答尽量简洁。  ",
        source_session_id="session-2",
    )

    reopened = MemoryRepository(database)
    assert duplicate.entry.id == first.entry.id
    assert duplicate.created is False
    assert reopened.get(first.entry.id, user_id="local") == first.entry
    assert len(reopened.list(user_id="local")) == 1


def test_remember_commits_an_index_outbox_event(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")

    remembered = repository.remember(
        user_id="local",
        project_key="project-a",
        scope=MemoryScope.PROJECT,
        kind=MemoryKind.HABIT,
        content="修改代码后运行 pytest。",
        source_session_id="session-1",
    )

    events = repository.claim_outbox(limit=10, lease_seconds=30)
    assert len(events) == 1
    assert events[0].memory_id == remembered.entry.id
    assert events[0].operation is IndexOperation.UPSERT
    assert events[0].memory_version == 1

    repository.mark_outbox_delivered(events[0].event_id)
    assert repository.claim_outbox(limit=10, lease_seconds=30) == []


def test_archive_hides_memory_and_enqueues_delete(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    remembered = repository.remember(
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.FEEDBACK,
        content="不要自动提交 Git。",
        source_session_id="session-1",
    )
    upsert = repository.claim_outbox(limit=1, lease_seconds=30)[0]
    repository.mark_outbox_delivered(upsert.event_id)

    assert repository.archive(remembered.entry.id, user_id="local") is True

    assert repository.get(remembered.entry.id, user_id="local") is None
    assert repository.search_lexical("自动提交", user_id="local", limit=5) == []
    delete_event = repository.claim_outbox(limit=1, lease_seconds=30)[0]
    assert delete_event.operation is IndexOperation.DELETE


def test_lexical_search_is_scoped_by_user_and_project(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.remember(
        user_id="alice",
        project_key="project-a",
        scope=MemoryScope.PROJECT,
        kind=MemoryKind.HABIT,
        content="发布前运行回归测试。",
        source_session_id="session-a",
    )
    repository.remember(
        user_id="bob",
        project_key="project-b",
        scope=MemoryScope.PROJECT,
        kind=MemoryKind.HABIT,
        content="发布前跳过回归测试。",
        source_session_id="session-b",
    )

    hits = repository.search_lexical(
        "发布 回归测试",
        user_id="alice",
        project_key="project-a",
        limit=5,
    )

    assert [hit.entry.user_id for hit in hits] == ["alice"]
    assert "运行" in hits[0].entry.content


def test_online_backup_integrity_and_restore_preserve_schema_and_crud(tmp_path):
    database = tmp_path / "memory.db"
    backup = tmp_path / "backups" / "memory.backup.db"
    repository = MemoryRepository(database)
    remembered = repository.remember(
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content="备份恢复后仍可读取。",
        source_session_id="session-1",
    ).entry

    result = repository.backup_online(backup)

    assert result.backup_path == backup
    assert result.integrity_check == "ok"
    assert "uv run reasoning-memory restore" in result.restore_steps
    restored = MemoryRepository.restore_from_backup(backup, tmp_path / "restored.db")
    assert restored.verify_integrity() == "ok"
    assert restored.get(remembered.id, user_id="local") == remembered
    created_after_restore = restored.remember(
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.HABIT,
        content="恢复后可以继续写入。",
        source_session_id="session-2",
    )
    assert created_after_restore.created is True



def test_repository_upgrades_existing_v1_database(tmp_path):
    database_path = tmp_path / "memory.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(_MIGRATION_V1)
        connection.execute("PRAGMA user_version=1")

    MemoryRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        journal = connection.execute(
            "SELECT name FROM sqlite_master WHERE name='memory_migration_journal'"
        ).fetchone()
    assert version == 4
    assert journal is not None
