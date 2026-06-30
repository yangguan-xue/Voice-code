from __future__ import annotations

from datetime import UTC
from pathlib import Path

from langchain_core.messages import HumanMessage

from voice_code.memory.index import (
    add_to_index,
    build_index,
    rebuild_index,
    remove_from_index,
)
from voice_code.memory.models import (
    MemoryEntry,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from voice_code.memory.paths import (
    ensure_memory_dirs,
    get_entries_dir_for_scope,
)
from voice_code.memory.retrieve import retrieve_memories
from voice_code.memory.store import (
    archive_entry,
    create_entry,
    delete_entry_file,
    get_entry,
    list_entries,
    read_memory_md,
)


def find_memory_file_path(entry_id: str, project_root: str | None = None) -> Path | None:
    for scope in ("user", "project"):
        if scope == "project" and not project_root:
            continue
        entries_dir = get_entries_dir_for_scope(scope, project_root)
        file_path = entries_dir / f"{entry_id}.md"
        if file_path.exists():
            return file_path.resolve()
    return None


class MemoryService:
    def __init__(self, project_root: str | None = None) -> None:
        self.project_root = project_root
        ensure_memory_dirs(project_root)

    def ensure_indexes(self) -> None:
        if not self._index_exists("user"):
            build_index("user")
        if self.project_root:
            if not self._index_exists("project"):
                build_index("project", self.project_root)

    def _index_exists(self, scope: str) -> bool:
        from voice_code.memory.index import index_exists

        return index_exists(scope, self.project_root if scope == "project" else None)

    def list(self, scope: str | None = None) -> list[MemoryEntry]:
        results: list[MemoryEntry] = []
        if scope is None or scope == "user":
            results.extend(list_entries("user"))
        if (scope is None or scope == "project") and self.project_root:
            results.extend(list_entries("project", self.project_root))
        return results

    def get(self, entry_id: str, scope: str) -> MemoryEntry | None:
        return get_entry(entry_id, scope, self.project_root if scope == "project" else None)

    def remember(
        self, text: str, session_id: str = "", entry_type: str = "reference"
    ) -> MemoryEntry:
        import hashlib
        from datetime import datetime

        timestamp = datetime.now(UTC)
        hash_input = f"{text}{timestamp.isoformat()}"
        entry_id = "mem_" + hashlib.sha256(hash_input.encode()).hexdigest()[:12]

        entry = MemoryEntry(
            id=entry_id,
            name=text[:60],
            type=MemoryType(entry_type),
            scope=MemoryScope.USER if not self.project_root else MemoryScope.PROJECT,
            description=text[:200],
            content=text,
            created_at=timestamp,
            updated_at=timestamp,
            source=MemorySource(kind="explicit", session_id=session_id),
            status=MemoryStatus.ACTIVE,
        )
        create_entry(entry, self.project_root)
        try:
            add_to_index(entry, self.project_root if entry.scope.value == "project" else None)
        except Exception:
            pass
        return entry

    def forget(self, entry_id: str, scope: str) -> bool:
        kw = dict(scope=scope)
        if scope == "project" and self.project_root:
            kw["project_root"] = self.project_root
        result = archive_entry(entry_id, **kw)
        if result:
            try:
                remove_from_index(entry_id, **kw)
            except Exception:
                pass
        return result

    def forget_hard(self, entry_id: str, scope: str) -> bool:
        kw = dict(scope=scope)
        if scope == "project" and self.project_root:
            kw["project_root"] = self.project_root
        result = delete_entry_file(entry_id, **kw)
        if result:
            try:
                remove_from_index(entry_id, **kw)
            except Exception:
                pass
        return result

    def retrieve_for_query(self, query: str, limit: int = 5) -> list[dict]:
        return retrieve_memories(query, self.project_root, limit=limit)

    def get_memory_messages(self, query: str = "", limit: int = 5) -> list[HumanMessage]:
        parts: list[str] = []

        user_md = read_memory_md("user")
        project_md = read_memory_md("project", self.project_root) if self.project_root else ""

        if user_md:
            parts.append(f"## User Memories\n\n{user_md}")
        if project_md:
            parts.append(f"## Project Memories\n\n{project_md}")

        if query.strip():
            selected = self.retrieve_for_query(query, limit=limit)
            if selected:
                parts.append("## Task-Relevant Memories")
                for mem in selected:
                    name = mem.get("name", "")
                    desc = mem.get("description", "")
                    content = mem.get("content", "")
                    tag_str = ""
                    if mem.get("tags"):
                        tag_str = f" [{', '.join(mem['tags'])}]"
                    parts.append(f"- **{name}**{tag_str}: {desc}")
                    if content and content != desc:
                        for line in content.split("\n")[:3]:
                            parts.append(f"  - {line}")

        if not parts:
            return []

        body = "\n\n".join(parts)
        msg = HumanMessage(
            content=f"<memories>\n{body}\n</memories>",
        )
        return [msg]

    def reindex(self) -> None:
        if self.project_root:
            rebuild_index("project", self.project_root)
        rebuild_index("user")

    def audit(self) -> list[dict]:
        issues: list[dict] = []
        all_entries = self.list()
        seen_names: dict[str, list[MemoryEntry]] = {}
        for entry in all_entries:
            if not entry.description.strip():
                issues.append({
                    "type": "empty_description",
                    "message": f"Entry '{entry.name}' ({entry.id}) has an empty description",
                    "entry_id": entry.id,
                })
            key = entry.name.lower().strip()
            if key in seen_names:
                issues.append({
                    "type": "duplicate_name",
                    "message": (
                        f"Duplicate name '{entry.name}' "
                        f"(ids: {seen_names[key][0].id}, {entry.id})"
                    ),
                    "entry_id": entry.id,
                })
            seen_names.setdefault(key, []).append(entry)
            from voice_code.memory.freshness import get_freshness_warning

            warning = get_freshness_warning(entry)
            if warning:
                issues.append({
                    "type": "stale",
                    "message": warning,
                    "entry_id": entry.id,
                })
        return issues
