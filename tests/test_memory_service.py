from __future__ import annotations

import pytest

from voice_code.memory.models import (
    MemoryEntry,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from voice_code.memory.service import MemoryService
from voice_code.memory.store import create_entry, delete_entry_file, list_entries


def test_memory_root_respects_reasoning_home(monkeypatch, tmp_path):
    from voice_code.memory.paths import get_memory_root

    reasoning_home = tmp_path / "reasoning-home"
    monkeypatch.setenv("REASONING_HOME", str(reasoning_home))

    assert get_memory_root() == reasoning_home / "memory"


@pytest.fixture
def memory_service(monkeypatch, tmp_path):
    monkeypatch.setenv("REASONING_HOME", str(tmp_path / "reasoning-home"))
    project_root = str(tmp_path / "project")
    from voice_code.memory.paths import ensure_memory_dirs

    ensure_memory_dirs(project_root)
    svc = MemoryService(project_root=project_root)
    yield svc
    for entry in list_entries("user"):
        delete_entry_file(entry.id, scope="user")
    for entry in list_entries("project", project_root):
        delete_entry_file(entry.id, scope="project", project_root=project_root)


def _make_entry(
    name: str = "测试记忆",
    description: str = "一段描述",
    scope: MemoryScope = MemoryScope.USER,
) -> MemoryEntry:
    import hashlib
    from datetime import UTC, datetime

    timestamp = datetime.now(UTC)
    entry_id = "mem_" + hashlib.sha256(f"{name}{timestamp.isoformat()}".encode()).hexdigest()[:12]
    return MemoryEntry(
        id=entry_id,
        name=name,
        type=MemoryType.REFERENCE,
        scope=scope,
        description=description,
        content=description,
        created_at=timestamp,
        updated_at=timestamp,
        source=MemorySource(kind="explicit", session_id="test"),
        status=MemoryStatus.ACTIVE,
    )


def test_audit_empty_description(memory_service):
    entry = _make_entry(description="")
    create_entry(entry, memory_service.project_root)

    issues = memory_service.audit()
    empty_desc_issues = [i for i in issues if i["type"] == "empty_description"]
    assert len(empty_desc_issues) == 1
    assert empty_desc_issues[0]["entry_id"] == entry.id
    assert entry.name in empty_desc_issues[0]["message"]


def test_audit_whitespace_description(memory_service):
    entry = _make_entry(description="   ")
    create_entry(entry, memory_service.project_root)

    issues = memory_service.audit()
    empty_desc_issues = [i for i in issues if i["type"] == "empty_description"]
    assert len(empty_desc_issues) == 1
    assert empty_desc_issues[0]["entry_id"] == entry.id


def test_is_stale_and_is_fresh():
    from datetime import UTC, datetime, timedelta

    from voice_code.memory.freshness import is_fresh, is_stale

    now = datetime(2026, 6, 29, tzinfo=UTC)
    fresh_entry = MemoryEntry(
        id="test1",
        name="fresh",
        updated_at=now - timedelta(days=5),
        freshness_days=30,
    )
    stale_entry = MemoryEntry(
        id="test2",
        name="stale",
        updated_at=now - timedelta(days=31),
        freshness_days=30,
    )

    assert is_fresh(fresh_entry, now) is True
    assert is_stale(fresh_entry, now) is False
    assert is_fresh(stale_entry, now) is False
    assert is_stale(stale_entry, now) is True


def test_audit_non_empty_description_no_issue(memory_service):
    entry = _make_entry(description="有内容的描述")
    create_entry(entry, memory_service.project_root)

    issues = memory_service.audit()
    empty_desc_issues = [i for i in issues if i["type"] == "empty_description"]
    assert len(empty_desc_issues) == 0
