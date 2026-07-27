"""Explicit mutable state for one agent-loop invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI


@dataclass(slots=True)
class AgentRuntimeState:
    messages: list[BaseMessage]
    active_model: ChatOpenAI
    turn: int = 0
    recovery_count: int = 0
    reactive_compact_attempted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def begin_turn(self) -> int:
        self.turn += 1
        self.recovery_count = 0
        self.reactive_compact_attempted = False
        return self.turn
