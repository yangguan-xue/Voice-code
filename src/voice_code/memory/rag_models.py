from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MemoryScope(StrEnum):
    USER = "user"
    PROJECT = "project"


class MemoryKind(StrEnum):
    PREFERENCE = "preference"
    FEEDBACK = "feedback"
    HABIT = "habit"
    USER_FACT = "user_fact"
    PROJECT_FACT = "project_fact"
    REFERENCE = "reference"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class MemoryAuthority(StrEnum):
    EXPLICIT = "explicit"
    APPROVED_EXTRACTION = "approved_extraction"
    AUTOMATIC_EXTRACTION = "automatic_extraction"
    MIGRATED = "migrated"


class MemoryReviewState(StrEnum):
    ACCEPTED = "accepted"
    CONFIRMATION_PENDING = "confirmation_pending"
    CONFLICT_PENDING = "conflict_pending"


class IndexOperation(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str
    user_id: str
    project_key: str | None
    scope: MemoryScope
    kind: MemoryKind
    content: str
    summary: str
    status: MemoryStatus
    source_session_id: str
    content_hash: str
    version: int
    created_at: datetime
    updated_at: datetime
    authority: MemoryAuthority = MemoryAuthority.EXPLICIT
    confidence: float | None = None
    supersedes_memory_id: str | None = None
    review_state: MemoryReviewState = MemoryReviewState.ACCEPTED
    last_recalled_at: datetime | None = None
    recall_count: int = 0


@dataclass(frozen=True, slots=True)
class RememberResult:
    entry: MemoryRecord
    created: bool


@dataclass(frozen=True, slots=True)
class MemoryHit:
    entry: MemoryRecord
    score: float
    backend: str


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: str
    memory_id: str
    operation: IndexOperation
    memory_version: int
    attempts: int


@dataclass(frozen=True, slots=True)
class VectorHit:
    memory_id: str
    memory_version: int
    score: float
