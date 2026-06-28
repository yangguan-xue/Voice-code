"""TUI message tree behavior tests."""

from __future__ import annotations

from reasoning_agent.tui import TurnBlock, TurnEntry
from reasoning_agent.tui_message_tree import (
    build_tui_rows_for_turn,
    set_turn_streaming_text,
    set_latest_bash_result_expanded,
)


def test_build_tui_rows_for_turn_keeps_historical_thinking_visible():
    turn = TurnBlock(
        turn_id=1,
        user_input="question",
        entries=[TurnEntry(kind="reasoning", text="first pass")],
        status="completed",
    )

    rows = build_tui_rows_for_turn(turn)

    assert [row.kind for row in rows] == ["user_input", "assistant_thinking"]
    assert rows[1].text == "first pass"


def test_set_latest_bash_result_expanded_respects_manual_user_state():
    turn = TurnBlock(
        turn_id=1,
        user_input="inspect",
        entries=[
            TurnEntry(
                kind="tool_pair",
                tool_name="bash",
                tool_call_id="old",
                tool_result="old result",
                is_result_collapsed=False,
                is_result_manual=True,
            ),
            TurnEntry(
                kind="tool_pair",
                tool_name="bash",
                tool_call_id="latest",
                tool_result="latest result",
                is_result_collapsed=True,
            ),
        ],
        status="completed",
    )

    set_latest_bash_result_expanded([turn], "latest")

    assert [entry.is_result_collapsed for entry in turn.entries] == [False, False]


def test_set_turn_streaming_text_finalizes_and_merges_live_entry():
    turn = TurnBlock(
        turn_id=1,
        user_input="question",
        entries=[TurnEntry(kind="text", text="partial answer")],
        status="streaming",
    )

    assert set_turn_streaming_text(turn, "draft", is_live=True) is True
    assert set_turn_streaming_text(turn, "final answer", is_live=False) is True

    assert [(entry.kind, entry.text, entry.is_live) for entry in turn.entries] == [
        ("text", "final answer", False),
    ]
