"""Pure TUI query-event state transition tests."""

from __future__ import annotations

from voice_code.agent.types import AgentEvent, EventType
from voice_code.tui_query_events import (
    apply_nonstream_event,
    apply_reasoning_event,
    apply_text_event,
    commit_streaming_buffer,
    extract_think_text,
)
from voice_code.tui_runtime import QueryRuntimeState


def test_extract_think_text_splits_visible_and_thinking_content():
    visible, thinking = extract_think_text("hello<think>a</think>world")

    assert visible == "helloworld"
    assert thinking == "a"


def test_apply_text_event_updates_pending_buffer_and_live_thinking():
    state = QueryRuntimeState(current_phase="thinking")

    update = apply_text_event(
        state,
        pending_text="",
        content="hello<think>reason</think>",
    )

    assert update.pending_text == "hello<think>reason</think>"
    assert update.visible_text == "hello"
    assert update.output_chars == len("hello<think>reason</think>")
    assert state.live_thinking_text == "reason"


def test_apply_reasoning_event_appends_to_live_thinking():
    state = QueryRuntimeState(live_thinking_text="a")

    apply_reasoning_event(state, "b")

    assert state.live_thinking_text == "ab"


def test_commit_streaming_buffer_returns_finalized_text_and_clears_runtime_thinking():
    state = QueryRuntimeState(live_thinking_text="reason")

    commit = commit_streaming_buffer(state, "hello<think>reason</think>")

    assert commit.visible_text == "hello"
    assert commit.thinking_text == "reason"
    assert state.live_thinking_text == ""


def test_apply_nonstream_event_tracks_tools_and_finish():
    state = QueryRuntimeState(current_phase="thinking")

    tool_call = apply_nonstream_event(
        state,
        AgentEvent(
            type=EventType.TOOL_CALL,
            turn=1,
            tool_name="read",
            tool_call_id="tc_1",
        ),
    )
    assert tool_call.kind == "tool_call"
    assert state.executing_tools == {"tc_1": "read"}

    tool_result = apply_nonstream_event(
        state,
        AgentEvent(
            type=EventType.TOOL_RESULT,
            turn=1,
            tool_name="read",
            tool_call_id="tc_1",
        ),
    )
    assert tool_result.kind == "tool_result"
    assert state.executing_tools == {}

    finish = apply_nonstream_event(
        state,
        AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed"),
    )
    assert finish.kind == "finish"
    assert state.current_phase == ""


def test_apply_nonstream_event_marks_error_and_clears_matching_tool():
    state = QueryRuntimeState(
        current_phase="fallback",
        executing_tools={"tc_1": "read", "tc_2": "write"},
    )

    result = apply_nonstream_event(
        state,
        AgentEvent(type=EventType.ERROR, turn=1, content="boom", tool_call_id="tc_1"),
    )

    assert result.kind == "error"
    assert state.current_phase == "error"
    assert state.executing_tools == {"tc_2": "write"}
