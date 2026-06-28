"""Agent 数据类型 — AgentEvent, EventType"""

from enum import Enum, auto

from pydantic import BaseModel, Field


class EventType(Enum):
    TEXT = auto()
    REASONING = auto()
    TOOL_CALL = auto()
    TOOL_RESULT = auto()
    ERROR = auto()
    FINISH = auto()


class AgentEvent(BaseModel):
    type: EventType
    turn: int
    content: str = ""
    phase: str = ""
    status: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    tool_args: dict[str, object] = Field(default_factory=dict)
    tool_result: str = ""
    finish_reason: str = ""  # "completed" | "max_turns" | "error"
