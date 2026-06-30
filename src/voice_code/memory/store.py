from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from voice_code.memory.frontmatter import format_memory_file, parse_memory_file
from voice_code.memory.models import MemoryEntry, MemoryStatus
from voice_code.memory.paths import (
    get_entries_dir_for_scope,
    get_project_memory_dir,
    get_user_memory_dir,
)


def create_entry(
    entry: MemoryEntry,
    project_root: str | None = None,
    tags_extra: list[str] | None = None,
) -> Path:
    if tags_extra:
        entry.tags.extend(tags_extra)
    entries_dir = get_entries_dir_for_scope(entry.scope.value, project_root)
    entries_dir.mkdir(parents=True, exist_ok=True)
    file_path = entries_dir / entry.file_name
    content = format_memory_file(entry)
    file_path.write_text(content, encoding="utf-8")
    _update_memory_md(entry.scope.value, project_root)
    return file_path


def get_entry(entry_id: str, scope: str, project_root: str | None = None) -> MemoryEntry | None:
    entries_dir = get_entries_dir_for_scope(scope, project_root)
    file_path = entries_dir / f"{entry_id}.md"
    return parse_memory_file(file_path)


def list_entries(
    scope: str,
    project_root: str | None = None,
    include_archived: bool = False,
) -> list[MemoryEntry]:
    entries_dir = get_entries_dir_for_scope(scope, project_root)
    if not entries_dir.is_dir():
        return []
    results: list[MemoryEntry] = []
    for f in sorted(entries_dir.iterdir()):
        if f.suffix != ".md":
            continue
        entry = parse_memory_file(f)
        if entry is None:
            continue
        if include_archived or entry.is_active():
            results.append(entry)
    return results


def update_entry(entry: MemoryEntry, project_root: str | None = None) -> Path | None:
    entry.updated_at = datetime.now(UTC)
    entries_dir = get_entries_dir_for_scope(entry.scope.value, project_root)
    file_path = entries_dir / entry.file_name
    if not file_path.exists():
        return None
    content = format_memory_file(entry)
    file_path.write_text(content, encoding="utf-8")
    _update_memory_md(entry.scope.value, project_root)
    return file_path


def archive_entry(entry_id: str, scope: str, project_root: str | None = None) -> bool:
    entry = get_entry(entry_id, scope, project_root)
    if entry is None:
        return False
    entry.status = MemoryStatus.ARCHIVED
    entry.updated_at = datetime.now(UTC)
    update_entry(entry, project_root)
    return True


def delete_entry_file(entry_id: str, scope: str, project_root: str | None = None) -> bool:
    entries_dir = get_entries_dir_for_scope(scope, project_root)
    file_path = entries_dir / f"{entry_id}.md"
    if not file_path.exists():
        return False
    file_path.unlink()
    _update_memory_md(scope, project_root)
    return True


_MEMORY_MD_MAX_LINES = 200
_MEMORY_MD_MAX_BYTES = 25 * 1024


def _update_memory_md(scope: str, project_root: str | None = None) -> None:
    if scope == "user":
        memory_dir = get_user_memory_dir()
    else:
        if project_root is None:
            return
        memory_dir = get_project_memory_dir(project_root)

    entries = list_entries(scope, project_root, include_archived=False)
    lines = ["# Memories", ""]
    for e in entries:
        lines.append(f"- [{e.name}](entries/{e.file_name}) — {e.description}")
    content = "\n".join(lines) + "\n"

    if len(lines) > _MEMORY_MD_MAX_LINES:
        lines = lines[:_MEMORY_MD_MAX_LINES]
        lines.append("")
        lines.append("*[Memory index truncated: too many entries]*")
        content = "\n".join(lines) + "\n"

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > _MEMORY_MD_MAX_BYTES:
        while len(content_bytes) > _MEMORY_MD_MAX_BYTES and lines:
            lines.pop()
            content = "\n".join(lines) + "\n"
            content_bytes = content.encode("utf-8")
        lines.append("*[Memory index truncated: too large]*")
        content = "\n".join(lines) + "\n"

    memory_md = memory_dir / "MEMORY.md"
    memory_md.write_text(content, encoding="utf-8")


def read_memory_md(scope: str, project_root: str | None = None) -> str:
    if scope == "user":
        memory_dir = get_user_memory_dir()
    else:
        if project_root is None:
            return ""
        memory_dir = get_project_memory_dir(project_root)

    memory_md = memory_dir / "MEMORY.md"
    if not memory_md.exists():
        return ""
    return memory_md.read_text(encoding="utf-8")
