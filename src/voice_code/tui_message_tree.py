"""TUI message tree primitives.

This module defines a message-row model that is independent from
the current Textual screen implementation. It is the first step toward
replacing the turn-level TUI with a message-level tree.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

TuiRowKind = Literal[
    "system_info",
    "system_error",
    "user_input",
    "assistant_text",
    "assistant_thinking",
    "assistant_tool_use",
    "user_tool_result",
]


@dataclass(frozen=True)
class TuiRow:
    """A single renderable row in the TUI message tree."""

    row_id: str
    turn_id: int
    kind: TuiRowKind
    text: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: str = ""
    tool_result_preview: str = ""
    is_result_collapsed: bool = True
    is_streaming: bool = False
    source_index: int = 0


@dataclass
class TuiEntry:
    kind: str
    text: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: str = ""
    tool_result_preview: str = ""
    is_result_collapsed: bool = True
    is_result_manual: bool = False
    is_live: bool = False


class _TurnLike(Protocol):
    turn_id: int
    user_input: str
    entries: Sequence[TuiEntry]
    status: str


class _MutableTurnLike(_TurnLike, Protocol):
    entries: list[TuiEntry]


def _find_trailing_entry_index(
    turn: _MutableTurnLike,
    kind: str,
    *,
    is_live: bool | None = None,
) -> int | None:
    for index in range(len(turn.entries) - 1, -1, -1):
        entry = turn.entries[index]
        entry_kind = getattr(entry, "kind", "")
        if entry_kind == "tool_pair":
            break
        if entry_kind != kind:
            continue
        if is_live is not None and getattr(entry, "is_live", False) != is_live:
            continue
        if is_live is None or getattr(entry, "is_live", False) == is_live:
            return index
    return None


def _clear_streaming_entry(turn: _MutableTurnLike, kind: str) -> bool:
    index = _find_trailing_entry_index(turn, kind, is_live=True)
    if index is None:
        return False
    del turn.entries[index]
    return True


def _finalize_streaming_entry(turn: _MutableTurnLike, kind: str, text: str) -> bool:
    live_index = _find_trailing_entry_index(turn, kind, is_live=True)
    finalized_index = _find_trailing_entry_index(turn, kind, is_live=False)
    if live_index is not None:
        live_entry = turn.entries[live_index]
        if finalized_index is not None and finalized_index < live_index:
            finalized_entry = turn.entries[finalized_index]
            if getattr(finalized_entry, "text", "") != text:
                finalized_entry.text = text
                del turn.entries[live_index]
                return True
            del turn.entries[live_index]
            return True
        if getattr(live_entry, "text", "") != text or getattr(live_entry, "is_live", False):
            live_entry.text = text
            live_entry.is_live = False
            return True
        return False
    if finalized_index is not None:
        finalized_entry = turn.entries[finalized_index]
        if getattr(finalized_entry, "text", "") != text or getattr(
            finalized_entry, "is_live", False
        ):
            finalized_entry.text = text
            finalized_entry.is_live = False
            return True
        return False
    turn.entries.append(TuiEntry(kind=kind, text=text, is_live=False))
    return True


def build_tui_rows(
    turns: Sequence[_TurnLike],
    current_turn: _TurnLike | None = None,
    *,
    hide_past_thinking: bool = False,
    last_visible_thinking_row_id: str | None = None,
) -> list[TuiRow]:
    """Flatten turn-shaped state into message-level rows."""
    ordered_turns = list(turns)
    if current_turn is not None:
        ordered_turns.append(current_turn)

    rows: list[TuiRow] = []
    for turn in ordered_turns:
        rows.extend(
            build_tui_rows_for_turn(
                turn,
                hide_past_thinking=hide_past_thinking,
                last_visible_thinking_row_id=last_visible_thinking_row_id,
            )
        )
    return rows


def build_tui_rows_for_turn(
    turn: _TurnLike,
    *,
    hide_past_thinking: bool = False,
    last_visible_thinking_row_id: str | None = None,
) -> list[TuiRow]:
    """Convert a single turn into ordered rows."""
    rows: list[TuiRow] = []
    is_streaming = turn.status == "streaming"

    if turn.user_input:
        rows.append(
            TuiRow(
                row_id=f"turn-{turn.turn_id}-user",
                turn_id=turn.turn_id,
                kind="user_input",
                text=turn.user_input,
                is_streaming=is_streaming,
            )
        )

    for index, entry in enumerate(turn.entries):
        kind = getattr(entry, "kind", "")
        text = getattr(entry, "text", "")
        tool_name = getattr(entry, "tool_name", "")
        tool_call_id = getattr(entry, "tool_call_id", "")
        tool_args = dict(getattr(entry, "tool_args", {}) or {})
        tool_result = getattr(entry, "tool_result", "")
        tool_result_preview = getattr(entry, "tool_result_preview", "")
        is_result_collapsed = getattr(entry, "is_result_collapsed", True)

        if kind == "info":
            rows.append(
                TuiRow(
                    row_id=f"turn-{turn.turn_id}-entry-{index}-info",
                    turn_id=turn.turn_id,
                    kind="system_info",
                    text=text,
                    is_streaming=is_streaming,
                    source_index=index,
                )
            )
            continue

        if kind == "error":
            rows.append(
                TuiRow(
                    row_id=f"turn-{turn.turn_id}-entry-{index}-error",
                    turn_id=turn.turn_id,
                    kind="system_error",
                    text=text,
                    is_streaming=is_streaming,
                    source_index=index,
                )
            )
            continue

        if kind == "reasoning":
            row_id = f"turn-{turn.turn_id}-entry-{index}-thinking"
            if (
                hide_past_thinking
                and last_visible_thinking_row_id is not None
                and row_id != last_visible_thinking_row_id
            ):
                continue
            rows.append(
                TuiRow(
                    row_id=row_id,
                    turn_id=turn.turn_id,
                    kind="assistant_thinking",
                    text=text,
                    is_streaming=is_streaming,
                    source_index=index,
                )
            )
            continue

        if kind == "text":
            rows.append(
                TuiRow(
                    row_id=f"turn-{turn.turn_id}-entry-{index}-text",
                    turn_id=turn.turn_id,
                    kind="assistant_text",
                    text=text,
                    is_streaming=is_streaming,
                    source_index=index,
                )
            )
            continue

        if kind == "tool_pair":
            rows.append(
                TuiRow(
                    row_id=f"turn-{turn.turn_id}-entry-{index}-tool-use",
                    turn_id=turn.turn_id,
                    kind="assistant_tool_use",
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    tool_args=tool_args,
                    tool_result=tool_result,
                    tool_result_preview=tool_result_preview,
                    is_result_collapsed=is_result_collapsed,
                    is_streaming=is_streaming,
                    source_index=index,
                )
            )
            if tool_result and not is_result_collapsed:
                rows.append(
                    TuiRow(
                        row_id=f"turn-{turn.turn_id}-entry-{index}-tool-result",
                        turn_id=turn.turn_id,
                        kind="user_tool_result",
                        text=tool_result,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        tool_args=tool_args,
                        tool_result=tool_result,
                        tool_result_preview=tool_result_preview,
                        is_result_collapsed=is_result_collapsed,
                        is_streaming=is_streaming,
                        source_index=index,
                    )
                )
            continue

    return rows


def set_turn_streaming_text(
    turn: _MutableTurnLike,
    text: str,
    *,
    is_live: bool = True,
) -> bool:
    if not text.strip():
        return _clear_streaming_entry(turn, "text")
    if not is_live:
        return _finalize_streaming_entry(turn, "text", text)
    index = _find_trailing_entry_index(turn, "text", is_live=True)
    if index is not None:
        entry = turn.entries[index]
        if getattr(entry, "text", "") != text:
            entry.text = text
            return True
        return False
    turn.entries.append(TuiEntry(kind="text", text=text, is_live=True))
    return True


def set_turn_streaming_thinking(
    turn: _MutableTurnLike,
    text: str,
    *,
    is_live: bool = True,
) -> bool:
    if not text.strip():
        return _clear_streaming_entry(turn, "reasoning")
    if not is_live:
        return _finalize_streaming_entry(turn, "reasoning", text)
    index = _find_trailing_entry_index(turn, "reasoning", is_live=True)
    if index is not None:
        entry = turn.entries[index]
        if getattr(entry, "text", "") != text:
            entry.text = text
            return True
        return False
    turn.entries.append(TuiEntry(kind="reasoning", text=text, is_live=True))
    return True


def append_turn_tool_call(
    turn: _MutableTurnLike,
    *,
    tool_name: str,
    tool_call_id: str,
    tool_args: dict[str, Any] | None = None,
) -> None:
    turn.entries.append(
        TuiEntry(
            kind="tool_pair",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_args=dict(tool_args or {}),
        )
    )


def set_turn_tool_result(
    turn: _MutableTurnLike,
    *,
    tool_name: str,
    tool_call_id: str,
    tool_result: str,
    tool_result_preview: str,
    is_result_collapsed: bool = True,
) -> bool:
    for entry in reversed(turn.entries):
        if getattr(entry, "kind", "") == "tool_pair" and getattr(
            entry, "tool_call_id", ""
        ) == tool_call_id:
            entry.tool_result = tool_result
            entry.tool_result_preview = tool_result_preview
            entry.is_result_collapsed = is_result_collapsed
            entry.is_result_manual = False
            if not getattr(entry, "tool_name", ""):
                entry.tool_name = tool_name
            return True
    turn.entries.append(
        TuiEntry(
            kind="tool_pair",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_result=tool_result,
            tool_result_preview=tool_result_preview,
            is_result_collapsed=is_result_collapsed,
            is_result_manual=False,
        )
    )
    return True


def set_turn_tool_result_collapsed(
    turn: _MutableTurnLike,
    tool_call_id: str,
    collapsed: bool,
    *,
    manual: bool = True,
) -> bool:
    for entry in reversed(turn.entries):
        if getattr(entry, "kind", "") == "tool_pair" and getattr(
            entry, "tool_call_id", ""
        ) == tool_call_id:
            if manual:
                entry.is_result_manual = True
            if getattr(entry, "is_result_collapsed", True) != collapsed:
                entry.is_result_collapsed = collapsed
                return True
            return False
    return False


def collapse_turn_tool_results(turn: _MutableTurnLike) -> None:
    for entry in turn.entries:
        if getattr(entry, "kind", "") == "tool_pair":
            entry.is_result_collapsed = True
            entry.is_result_manual = True


def find_latest_bash_tool_call_id(
    turns: Sequence[_TurnLike],
    current_turn: _TurnLike | None = None,
) -> str | None:
    ordered_turns = list(turns)
    if current_turn is not None:
        ordered_turns.append(current_turn)

    for turn in reversed(ordered_turns):
        for entry in reversed(turn.entries):
            if (
                getattr(entry, "kind", "") == "tool_pair"
                and getattr(entry, "tool_name", "") == "bash"
                and getattr(entry, "tool_result", "")
            ):
                return getattr(entry, "tool_call_id", "") or None
    return None


def find_last_thinking_row_id(
    turns: Sequence[_TurnLike],
    current_turn: _TurnLike | None = None,
) -> str | None:
    ordered_turns = list(turns)
    if current_turn is not None:
        ordered_turns.append(current_turn)

    for turn in reversed(ordered_turns):
        for index in range(len(turn.entries) - 1, -1, -1):
            entry = turn.entries[index]
            if getattr(entry, "kind", "") == "reasoning" and getattr(
                entry, "text", ""
            ).strip():
                return f"turn-{turn.turn_id}-entry-{index}-thinking"
    return None


def set_latest_bash_result_expanded(
    turns: Sequence[_MutableTurnLike],
    tool_call_id: str | None,
) -> None:
    for turn in turns:
        for entry in turn.entries:
            if getattr(entry, "kind", "") != "tool_pair":
                continue
            if getattr(entry, "tool_name", "") != "bash":
                continue
            if not getattr(entry, "tool_result", ""):
                continue
            if getattr(entry, "is_result_manual", False):
                continue
            entry.is_result_collapsed = getattr(entry, "tool_call_id", "") != tool_call_id
