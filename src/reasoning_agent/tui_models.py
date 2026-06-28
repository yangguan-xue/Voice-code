"""Structured TUI message models.

These classes are the Textual-side equivalent of cc-haha's normalized
message layer: the screen owns a list of turn/message records, while renderers
decide how each entry appears.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reasoning_agent.transcript_view import PlainTurn, PlainTurnEntry


@dataclass
class TurnEntry:
    """One renderable entry inside a turn."""

    kind: str
    text: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: str = ""
    tool_result_preview: str = ""
    is_result_collapsed: bool = True


@dataclass
class TurnBlock:
    """A complete user turn and the assistant/tool messages it produced."""

    turn_id: int
    user_input: str
    entries: list[TurnEntry] = field(default_factory=list)
    status: str = "completed"


def turn_to_plain(turn: TurnBlock) -> PlainTurn:
    """Convert a TUI turn to the shared plain-text export model."""
    return PlainTurn(
        turn_id=turn.turn_id,
        user_input=turn.user_input,
        entries=[
            PlainTurnEntry(
                kind=entry.kind,
                text=entry.text,
                tool_name=entry.tool_name,
                tool_args=entry.tool_args,
                tool_result=entry.tool_result,
                tool_result_preview=entry.tool_result_preview,
            )
            for entry in turn.entries
        ],
    )
