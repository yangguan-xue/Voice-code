from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal


class MemoryType(StrEnum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"
    CONFIG = "config"


class MemoryScope(StrEnum):
    USER = "user"
    PROJECT = "project"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    IGNORED = "ignored"


MemorySourceKind = Literal["explicit", "extracted", "migrated"]


@dataclass
class MemorySource:
    kind: MemorySourceKind = "explicit"
    session_id: str = ""


@dataclass
class MemoryEntry:
    id: str
    name: str
    type: MemoryType = MemoryType.REFERENCE
    scope: MemoryScope = MemoryScope.PROJECT
    description: str = ""
    tags: list[str] = field(default_factory=list)
    content: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: MemorySource = field(default_factory=MemorySource)
    status: MemoryStatus = MemoryStatus.ACTIVE
    freshness_days: int = 30

    def is_active(self) -> bool:
        return self.status == MemoryStatus.ACTIVE

    @property
    def file_name(self) -> str:
        return f"{self.id}.md"
