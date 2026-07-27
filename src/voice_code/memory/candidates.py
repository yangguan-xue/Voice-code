from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from voice_code.memory.rag_models import (
    MemoryAuthority as MemoryAuthority,
)
from voice_code.memory.rag_models import (
    MemoryKind,
    MemoryScope,
)
from voice_code.memory.rag_models import (
    MemoryReviewState as MemoryReviewState,
)

_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


class SensitivityDecision(StrEnum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    NEEDS_CONFIRMATION = "needs_confirmation"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CONFIRMATION_PENDING = "confirmation_pending"


class MutationAction(StrEnum):
    CREATE = "create"
    MERGE = "merge"
    SUPERSEDE = "supersede"
    CONFLICT = "conflict"
    IGNORE_DUPLICATE = "ignore_duplicate"
    REJECT = "reject"
    REQUIRE_CONFIRMATION = "require_confirmation"


class ExtractionMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    REVIEW = "review"
    AUTOMATIC = "automatic"


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    candidate_id: str
    user_id: str
    project_key: str | None
    scope: MemoryScope
    kind: MemoryKind
    content: str
    confidence: float
    evidence_text: str
    source_session_id: str
    source_turn_id: str
    extraction_run_id: str
    extractor_model: str
    sensitivity: SensitivityDecision
    created_at: datetime

    def __post_init__(self) -> None:
        normalized = " ".join(self.content.split()).strip()
        object.__setattr__(self, "content", normalized)
        if not 4 <= len(normalized) <= 1_000:
            raise ValueError("Invalid candidate content")
        evidence = " ".join(self.evidence_text.split()).strip()
        object.__setattr__(self, "evidence_text", evidence)
        if not evidence or len(evidence) > 500:
            raise ValueError("Invalid candidate evidence_text")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Invalid candidate confidence")
        for name in ("candidate_id", "extraction_run_id"):
            if _ID_PATTERN.fullmatch(getattr(self, name)) is None:
                raise ValueError(f"Invalid candidate {name}")
        for name in ("user_id", "source_session_id", "source_turn_id", "extractor_model"):
            value = getattr(self, name).strip()
            if not value or len(value) > 128:
                raise ValueError(f"Invalid candidate {name}")
        if self.scope is MemoryScope.PROJECT and not self.project_key:
            raise ValueError("Project candidate requires project_key")
        if self.project_key is not None and len(self.project_key) > 128:
            raise ValueError("Invalid candidate project_key")


@dataclass(frozen=True, slots=True)
class MemoryMutationDecision:
    action: MutationAction
    candidate_id: str
    target_memory_id: str | None
    reason_code: str
    similarity: float | None = None
    requires_user_action: bool = False

    def __post_init__(self) -> None:
        if _ID_PATTERN.fullmatch(self.candidate_id) is None:
            raise ValueError("Invalid mutation candidate_id")
        if self.target_memory_id and _ID_PATTERN.fullmatch(self.target_memory_id) is None:
            raise ValueError("Invalid mutation target_memory_id")
        if not self.reason_code or len(self.reason_code) > 64:
            raise ValueError("Invalid mutation reason_code")
        if self.similarity is not None and (
            not math.isfinite(self.similarity) or not 0 <= self.similarity <= 1
        ):
            raise ValueError("Invalid mutation similarity")


@dataclass(frozen=True, slots=True)
class StoredMemoryCandidate:
    candidate: MemoryCandidate
    status: CandidateStatus
    reason_code: str | None = None
    decided_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExtractionJob:
    job_id: str
    user_id: str
    project_key: str | None
    source_session_id: str
    source_turn_id: str
    user_input: str
    assistant_response: str
    mode: ExtractionMode
    attempts: int
