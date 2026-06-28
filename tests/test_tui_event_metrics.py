"""TUI event metric mapping tests."""

from __future__ import annotations

from voice_code.agent.types import AgentEvent, EventType
from voice_code.tui import AgentScreen
from voice_code.tui_runtime import QueryRuntimeState, build_runtime_display, phase_label


def test_apply_error_event_metrics_prefers_status_and_ignores_generic_error():
    screen = AgentScreen()
    screen._metric_compact_count = 0
    screen._metric_fallback_count = 0
    screen._metric_resume_count = 0

    screen._apply_error_event_metrics(
        AgentEvent(type=EventType.ERROR, turn=1, content="anything", status="compact")
    )
    screen._apply_error_event_metrics(
        AgentEvent(type=EventType.ERROR, turn=1, content="anything", status="fallback")
    )
    screen._apply_error_event_metrics(
        AgentEvent(type=EventType.ERROR, turn=1, content="anything", status="resume")
    )
    screen._apply_error_event_metrics(
        AgentEvent(type=EventType.ERROR, turn=1, content="plain failure", status="generic_error")
    )

    assert screen._metric_compact_count == 1
    assert screen._metric_fallback_count == 1
    assert screen._metric_resume_count == 1


def test_route_error_event_treats_compact_as_info_not_error():
    screen = AgentScreen()
    entries: list[tuple[str, str]] = []
    screen._append_status_to_current_turn = lambda text: entries.append(("info", text))  # type: ignore[method-assign]
    screen._append_tool_error = lambda event: entries.append(("tool", event.content))  # type: ignore[method-assign]
    screen._append_error_to_current_turn = lambda text: entries.append(("error", text))  # type: ignore[method-assign]

    screen._route_error_event(
        AgentEvent(type=EventType.ERROR, turn=1, content="Conversation compacted", status="compact")
    )

    assert entries == [("info", "Conversation compacted")]


def test_phase_label_maps_known_runtime_phases():
    assert phase_label("thinking") == "Thinking…"
    assert phase_label("compacting") == "Compacting…"
    assert phase_label("fallback") == "Retrying with fallback model…"
    assert phase_label("resuming") == "Resuming after token limit…"
    assert phase_label("custom") == "custom"


def test_build_runtime_display_summarizes_state():
    thinking = build_runtime_display(
        QueryRuntimeState(
            current_phase="fallback",
            live_thinking_text="thinking now",
            executing_tools={"tc_1": "read"},
        )
    )
    assert thinking.thinking_text == "thinking now"
    assert thinking.status_phase == "fallback"
    assert thinking.tool_count == 1

    executing = build_runtime_display(
        QueryRuntimeState(
            executing_tools={
                "tc_1": "read",
                "tc_2": "write",
                "tc_3": "grep",
                "tc_4": "glob",
            }
        )
    )
    assert executing.thinking_text == "Executing 4 tools: read, write, grep +1 more"
    assert executing.status_phase == "executing"
    assert executing.tool_count == 4

    phase_only = build_runtime_display(QueryRuntimeState(current_phase="resuming"))
    assert phase_only.thinking_text == "Resuming after token limit…"
    assert phase_only.status_phase == "resuming"
    assert phase_only.tool_count == 0

    empty = build_runtime_display(QueryRuntimeState())
    assert empty.thinking_text == ""
    assert empty.status_phase == ""
    assert empty.tool_count == 0


def test_render_runtime_thinking_state_prefers_live_thinking_text():
    screen = AgentScreen()
    status_calls: list[tuple] = []

    class DummyWidget:
        def __init__(self) -> None:
            self.last = None

        def update(self, value) -> None:
            self.last = value

    widget = DummyWidget()
    state = QueryRuntimeState(
        current_phase="fallback",
        live_thinking_text="thinking now",
        executing_tools={"tc_1": "read"},
    )
    screen._render_thinking_block = lambda text: text  # type: ignore[method-assign]
    screen._update_statusbar = lambda *args: status_calls.append(args)  # type: ignore[method-assign]

    screen._render_runtime_thinking_state(
        thinking_widget=widget,  # type: ignore[arg-type]
        state=state,
    )

    assert widget.last == "thinking now"
    assert status_calls[-1] == ("fallback", 1)


def test_render_runtime_thinking_state_shows_executing_tools_when_no_live_text():
    screen = AgentScreen()
    status_calls: list[tuple] = []

    class DummyWidget:
        def __init__(self) -> None:
            self.last = None

        def update(self, value) -> None:
            self.last = value

    widget = DummyWidget()
    state = QueryRuntimeState(
        executing_tools={"tc_1": "read", "tc_2": "write"},
    )
    screen._render_thinking_block = lambda text: text  # type: ignore[method-assign]
    screen._update_statusbar = lambda *args: status_calls.append(args)  # type: ignore[method-assign]

    screen._render_runtime_thinking_state(
        thinking_widget=widget,  # type: ignore[arg-type]
        state=state,
    )

    assert "Executing 2 tools" in widget.last
    assert status_calls[-1] == ("executing", 2)


def test_action_interrupt_turn_triggers_abort_signal_when_busy():
    screen = AgentScreen()
    entries: list[str] = []
    screen._append_info_to_current_turn = lambda text: entries.append(text)  # type: ignore[method-assign]
    screen._is_busy = True

    screen.action_interrupt_turn()

    assert screen._abort_signal.is_triggered() is True
    assert entries == ["Interrupt requested…"]


def test_action_interrupt_turn_reports_when_idle():
    screen = AgentScreen()
    messages: list[str] = []
    screen._append_system_info = lambda text: messages.append(text)  # type: ignore[method-assign]
    screen._is_busy = False

    screen.action_interrupt_turn()

    assert screen._abort_signal.is_triggered() is False
    assert messages == ["No running turn to interrupt."]
