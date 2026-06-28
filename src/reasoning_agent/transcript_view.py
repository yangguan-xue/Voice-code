"""Shared plain-text transcript helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlainTurnEntry:
    kind: str
    text: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: str = ""
    tool_result_preview: str = ""
    is_result_collapsed: bool = True


@dataclass
class PlainTurn:
    turn_id: int
    user_input: str
    entries: list[PlainTurnEntry] = field(default_factory=list)


def last_assistant_text(turns: Sequence[PlainTurn]) -> str:
    for turn in reversed(turns):
        for entry in reversed(turn.entries):
            if entry.kind == "text" and entry.text.strip():
                return entry.text
    return ""


def _clean_tool_use_error(text: str) -> str:
    return (
        text.replace("<tool_use_error>", "")
        .replace("</tool_use_error>", "")
        .strip()
    )


def render_turn_as_plain_text(turn: PlainTurn) -> str:
    lines: list[str] = []
    if turn.user_input:
        lines.append(f"> {turn.user_input}")
        lines.append("")
    for entry in turn.entries:
        if entry.kind == "text":
            lines.append(entry.text)
            lines.append("")
        elif entry.kind == "tool_pair":
            args = ", ".join(f"{k}={v!r}" for k, v in entry.tool_args.items())
            lines.append(f"[tool] {entry.tool_name}({args})")
            if entry.tool_result.startswith("<tool_use_error>"):
                lines.append(_clean_tool_use_error(entry.tool_result))
            elif entry.tool_result and entry.is_result_collapsed and entry.tool_result_preview:
                lines.append(entry.tool_result_preview)
            elif entry.tool_result and not entry.is_result_collapsed:
                lines.append(entry.tool_result)
            lines.append("")
        elif entry.kind == "error":
            lines.append(f"[error] {entry.text[:300]}")
            lines.append("")
        elif entry.kind == "reasoning":
            lines.append(f"[thinking] {entry.text[:500]}")
            lines.append("")
        elif entry.kind == "info":
            lines.append(f"[info] {entry.text}")
            lines.append("")
    return "\n".join(lines).strip()


def render_turns_as_plain_text(turns: Sequence[PlainTurn]) -> str:
    parts = [render_turn_as_plain_text(turn) for turn in turns if turn.entries or turn.user_input]
    return "\n\n---\n\n".join(part for part in parts if part).strip()
