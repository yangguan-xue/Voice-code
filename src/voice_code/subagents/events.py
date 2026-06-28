"""子 agent task 生命周期事件。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from voice_code.subagents.types import TaskStatus


class TaskEventType(StrEnum):
    CREATED = "created"
    STARTED = "started"
    PROGRESS = "progress"
    WAITING_PERMISSION = "waiting_permission"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STOP_REQUESTED = "stop_requested"


@dataclass(slots=True)
class TaskEvent:
    type: TaskEventType
    task_id: str
    session_id: str
    agent_type: str
    status: TaskStatus
    timestamp: float
    message: str = ""
