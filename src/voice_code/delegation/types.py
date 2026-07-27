"""Contracts for voice and text background task delegation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class DelegationTaskStatus(StrEnum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ACCEPTED_INTO_WORKSPACE = "accepted_into_workspace"
    DISCARDED = "discarded"


@dataclass(slots=True)
class DelegationBrief:
    task_id: str
    intent: str
    workspace: str
    branch: str = ""
    relevant_files: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    risk_level: str = "low"
    confirmation_required: bool = False
    context_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DelegationTask:
    brief: DelegationBrief
    status: DelegationTaskStatus = DelegationTaskStatus.ACCEPTED
    worktree_path: str = ""
    result_summary: str = ""
    patch_path: str = ""
    error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = str(self.status)
        return payload
