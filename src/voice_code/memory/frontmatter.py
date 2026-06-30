from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from voice_code.memory.models import (
    MemoryEntry,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    MemoryType,
)

_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _serialize_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        items = ", ".join(_serialize_value(v) for v in value)
        return f"[{items}]"
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _serialize_frontmatter(entry: MemoryEntry) -> str:
    fields = [
        ("id", entry.id),
        ("name", entry.name),
        ("type", entry.type.value),
        ("scope", entry.scope.value),
        ("description", entry.description),
        ("tags", entry.tags),
        ("created_at", entry.created_at),
        ("updated_at", entry.updated_at),
        ("source_kind", entry.source.kind),
        ("source_session_id", entry.source.session_id),
        ("status", entry.status.value),
        ("freshness_days", entry.freshness_days),
    ]
    lines = ["---"]
    for key, value in fields:
        if isinstance(value, list):
            if value:
                items = ", ".join(v for v in value)
                lines.append(f"{key}: [{items}]")
            else:
                lines.append(f"{key}: []")
        elif isinstance(value, datetime):
            lines.append(f"{key}: {value.isoformat()}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def parse_memory_file(path: Path) -> MemoryEntry | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return parse_memory_text(text)


def parse_memory_text(text: str) -> MemoryEntry | None:
    m = _FRONTMATTER_PATTERN.match(text)
    if not m:
        return None
    raw = m.group(1)
    body = text[m.end() :].strip()

    fm = _parse_yaml_like(raw)
    if not fm.get("id"):
        return None

    try:
        return MemoryEntry(
            id=str(fm["id"]),
            name=str(fm.get("name", "")),
            type=_parse_enum(fm.get("type", ""), MemoryType, MemoryType.REFERENCE),
            scope=_parse_enum(fm.get("scope", ""), MemoryScope, MemoryScope.PROJECT),
            description=str(fm.get("description", "")),
            tags=_parse_tag_list(fm.get("tags", [])),
            content=body,
            created_at=_parse_dt(fm.get("created_at")),
            updated_at=_parse_dt(fm.get("updated_at")),
            source=MemorySource(
                kind=str(fm.get("source_kind", "explicit")),
                session_id=str(fm.get("source_session_id", "")),
            ),
            status=_parse_enum(fm.get("status", "active"), MemoryStatus, MemoryStatus.ACTIVE),
            freshness_days=int(fm.get("freshness_days", 30)),
        )
    except (ValueError, TypeError):
        return None


def format_memory_file(entry: MemoryEntry) -> str:
    front = _serialize_frontmatter(entry)
    body = entry.content or entry.description
    return f"{front}\n\n{body}\n"


def _parse_yaml_like(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            items = [v.strip() for v in inner.split(",") if v.strip()]
            result[key] = items
        else:
            result[key] = value
    return result


def _parse_enum(value: str, enum_cls: type, default) -> Any:
    for member in enum_cls:
        if member.value == value:
            return member
    return default


def _parse_tag_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            return [v.strip() for v in inner.split(",") if v.strip()]
    return []


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass

    return datetime.now(UTC)
