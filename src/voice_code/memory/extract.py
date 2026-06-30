from __future__ import annotations

from datetime import UTC, datetime

from voice_code.memory.models import (
    MemoryEntry,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from voice_code.memory.service import MemoryService
from voice_code.memory.store import list_entries

_EXTRACT_TRIGGER_TYPES = {"feedback", "project", "reference"}

_EXTRACT_KEYWORDS = {
    "feedback": [
        "remember", "always", "never", "prefer", "should", "must",
        "important", "记住", "总是", "不要", "应该", "习惯",
    ],
    "project": [
        "project", "config", "setup", "deploy", "release", "build",
        "项目", "配置", "部署", "发布", "仓库",
    ],
    "reference": [
        "url", "link", "api", "endpoint", "dashboard", "port",
        "地址", "链接", "端口",
    ],
}


def extract_memories(
    transcript_text: str,
    service: MemoryService,
    session_id: str = "",
) -> list[MemoryEntry]:
    if not transcript_text.strip():
        return []

    project_entries = list_entries("project", service.project_root) if service.project_root else []
    existing = list_entries("user") + project_entries
    existing_names = {e.name.lower().strip() for e in existing}

    candidates: list[MemoryEntry] = []

    for mem_type, keywords in _EXTRACT_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in transcript_text.lower():
                candidate = _build_candidate(
                    transcript_text, mem_type, session_id, service.project_root,
                )
                if candidate and candidate.name.lower().strip() not in existing_names:
                    candidates.append(candidate)
                    existing_names.add(candidate.name.lower().strip())
                break

    return candidates


def _build_candidate(
    text: str,
    mem_type: str,
    session_id: str,
    project_root: str | None,
) -> MemoryEntry | None:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return None

    first_line = lines[0][:80]
    description = first_line[:200]
    import hashlib

    timestamp = datetime.now(UTC)
    raw = f"{first_line}{timestamp.isoformat()}"
    entry_id = "mem_ext_" + hashlib.sha256(raw.encode()).hexdigest()[:12]

    return MemoryEntry(
        id=entry_id,
        name=first_line[:60],
        type=MemoryType(mem_type),
        scope=MemoryScope.PROJECT if project_root else MemoryScope.USER,
        description=description,
        content=text[:1000],
        created_at=timestamp,
        updated_at=timestamp,
        source=MemorySource(kind="extracted", session_id=session_id),
        status=MemoryStatus.ACTIVE,
    )


def conservative_extract_and_save(
    transcript_text: str,
    service: MemoryService,
    session_id: str = "",
) -> int:
    candidates = extract_memories(transcript_text, service, session_id)
    saved = 0
    for entry in candidates:
        try:
            from voice_code.memory.store import create_entry
            create_entry(entry, service.project_root)
            from voice_code.memory.index import add_to_index
            add_to_index(entry, service.project_root if entry.scope.value == "project" else None)
            saved += 1
        except Exception:
            pass
    return saved
