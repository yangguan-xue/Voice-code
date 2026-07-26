"""Stable contracts for GoalLoop persistence and decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class GoalStatus(StrEnum):
    CREATED = "created"
    BUILDING = "building"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    DECIDING = "deciding"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class GoalAction(StrEnum):
    CONTINUE = "continue"
    FINISH = "finish"
    ROLLBACK = "rollback"
    ESCALATE = "escalate"
    STOP = "stop"


@dataclass(slots=True)
class GoalSpec:
    goal_id: str
    objective: str
    workspace: str
    verification_commands: list[str]
    allowed_paths: list[str] = field(default_factory=list)
    max_iterations: int = 5
    max_seconds: int = 7200
    max_consecutive_same_failure: int = 3
    min_review_iterations: int = 5
    require_review: bool = True
    baseline_ref: str = ""
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VerificationEvidence:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GoalDecision:
    action: GoalAction
    reason: str
    next_prompt: str = ""
    failure_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["action"] = str(self.action)
        return payload


@dataclass(slots=True)
class GoalState:
    goal_id: str
    status: GoalStatus = GoalStatus.CREATED
    iteration: int = 0
    started_at: float = 0.0
    updated_at: float = 0.0
    last_prompt: str = ""
    last_result: str = ""
    last_failure_signature: str = ""
    repeated_failure_count: int = 0
    stop_requested: bool = False
    execution_workspace: str = ""
    baseline_ref: str = ""
    last_checkpoint_ref: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    sequence: int = 0
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = str(self.status)
        return payload


@dataclass(frozen=True, slots=True)
class GoalBuildRequest:
    prompt: str
    workspace: str
    iteration: int
    baseline_ref: str
