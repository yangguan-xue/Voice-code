"""子 agent 任务核心类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_PERMISSION = "waiting_permission"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class TaskProgress:
    tool_use_count: int = 0
    token_count: int = 0
    last_activity: str = ""
    summary: str = ""


@dataclass(slots=True)
class TaskSummary:
    summary: str
    transcript_path: str
    total_tokens: int = 0
    tool_uses: int = 0
    duration_ms: int = 0


@dataclass(slots=True)
class AgentTask:
    task_id: str
    session_id: str
    parent_task_id: str | None
    parent_session_id: str | None
    agent_type: str
    description: str
    prompt: str
    status: TaskStatus
    model_name: str | None
    transcript_path: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    progress: TaskProgress | None = None
    result_summary: str | None = None
    error: str | None = None
    stop_requested: bool = False
