"""TUI event metric mapping tests."""

from __future__ import annotations

import queue
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from voice_code.agent.types import AgentEvent, EventType
from voice_code.commands import ResumeSessionResult
from voice_code.session.state import build_session_runtime_state
from voice_code.subagents.types import AgentTask, TaskStatus
from voice_code.tui import AgentScreen
from voice_code.tui_runtime import (
    QueryRuntimeState,
    build_runtime_display,
    build_task_display,
    phase_label,
)
from voice_code.tui_sessions import SessionSelected


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


@pytest.mark.asyncio
async def test_goal_event_queue_renders_text_and_keeps_turn_open():
    screen = AgentScreen()
    screen._metric_output_chars = 0
    screen._current_turn_has_live_thinking = False
    previews: list[tuple[str, bool]] = []
    thinking: list[str] = []
    status_calls: list[tuple[str, int]] = []
    finished: list[bool] = []

    screen._set_streaming_preview = lambda text: previews.append((text, True))  # type: ignore[method-assign]
    screen._set_streaming_preview_final = lambda text: previews.append((text, False))  # type: ignore[method-assign]
    screen._set_streaming_thinking = lambda text, **_kwargs: thinking.append(text)  # type: ignore[method-assign]
    screen._update_statusbar = (  # type: ignore[method-assign]
        lambda phase="", tool_count=0: status_calls.append((phase, tool_count))
    )
    screen._is_near_bottom = lambda: True  # type: ignore[method-assign]
    screen._scroll_to_bottom = lambda **_kwargs: None  # type: ignore[method-assign]
    screen._append_tool_call = lambda _event: None  # type: ignore[method-assign]
    screen._append_tool_result = lambda _event: None  # type: ignore[method-assign]
    screen._route_error_event = lambda _event: None  # type: ignore[method-assign]
    screen._finish_turn = lambda: finished.append(True)  # type: ignore[method-assign]

    event_queue: queue.Queue[AgentEvent | Exception | None] = queue.Queue()
    event_queue.put(AgentEvent(type=EventType.REASONING, turn=1, content="checking files"))
    event_queue.put(AgentEvent(type=EventType.TEXT, turn=1, content="visible progress"))
    event_queue.put(AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed"))
    event_queue.put(None)

    result = await screen._consume_agent_event_queue(
        event_queue,
        finish_turn_on_finish=False,
    )

    assert result == "visible progress"
    assert screen._metric_output_chars == len("visible progress")
    assert any("checking files" in item for item in thinking)
    assert ("visible progress", True) in previews
    assert ("visible progress", False) in previews
    assert status_calls
    assert finished == []


def test_resume_into_current_view_reuses_resume_semantics(monkeypatch: pytest.MonkeyPatch):
    screen = AgentScreen()
    screen._turns = []
    screen._current_turn = None
    screen._current_turn_has_live_thinking = False
    screen._sticky_follow = False
    screen._pending_paste_text = "pending"
    screen._showing_paste_summary = True
    screen._model = SimpleNamespace(model_name="voice-code-pro")
    screen._transcript_writer = SimpleNamespace(close=lambda: None)
    screen._messages_to_turns = lambda messages: ["rebuilt"]  # type: ignore[method-assign]
    screen._refresh_history = lambda: None  # type: ignore[method-assign]
    screen._update_statusbar = lambda *args: None  # type: ignore[method-assign]
    screen._update_session_sidebar = lambda: None  # type: ignore[method-assign]
    screen._update_composer_meta = lambda *args: None  # type: ignore[method-assign]
    screen._append_system_info = lambda *args: None  # type: ignore[method-assign]
    screen.query_one = lambda *_args, **_kwargs: SimpleNamespace(focus=lambda: None)  # type: ignore[method-assign]

    resumed = ResumeSessionResult(
        session_id="session-xyz",
        messages=[HumanMessage(content="hello"), AIMessage(content="world")],
        transcript_writer=SimpleNamespace(),
    )
    monkeypatch.setattr("voice_code.tui.resume_session", lambda session_id: resumed)
    monkeypatch.setattr(
        "voice_code.tui.get_or_create_service",
        lambda session_id, event_loop: f"service:{session_id}",
    )
    monkeypatch.setattr(
        "voice_code.tui.asyncio.get_running_loop",
        lambda: SimpleNamespace(),
    )

    assert screen._resume_into_current_view("session-xyz") is True
    assert screen._turns == ["rebuilt"]
    assert screen._resume_messages == resumed.messages
    assert screen._session_id == "session-xyz"
    assert screen._subagent_service == "service:session-xyz"
    assert screen._sticky_follow is True
    assert screen._pending_paste_text == ""
    assert screen._showing_paste_summary is False


def test_resume_into_current_view_restores_runtime_metadata(monkeypatch: pytest.MonkeyPatch):
    screen = AgentScreen()
    screen._turns = []
    screen._current_turn = None
    screen._current_turn_has_live_thinking = False
    screen._sticky_follow = False
    screen._pending_paste_text = ""
    screen._showing_paste_summary = False
    screen._model = SimpleNamespace(model_name="voice-code-pro")
    screen._cwd = "/Users/current/workspace"
    screen._transcript_writer = SimpleNamespace(close=lambda: None)
    screen._messages_to_turns = lambda messages: ["rebuilt"]  # type: ignore[method-assign]
    screen._refresh_history = lambda: None  # type: ignore[method-assign]
    screen._update_statusbar = lambda *args: None  # type: ignore[method-assign]
    screen._update_session_sidebar = lambda: None  # type: ignore[method-assign]
    meta_messages: list[str] = []
    screen._update_composer_meta = (
        lambda message=None: meta_messages.append(message or "")  # type: ignore[method-assign]
    )
    screen._append_system_info = lambda *args: None  # type: ignore[method-assign]
    screen.query_one = lambda *_args, **_kwargs: SimpleNamespace(focus=lambda: None)  # type: ignore[method-assign]

    resumed = ResumeSessionResult(
        session_id="session-xyz",
        messages=[HumanMessage(content="hello")],
        transcript_writer=SimpleNamespace(),
        runtime_state=build_session_runtime_state(
            session_id="session-xyz",
            cwd="/Users/example/workspace/voice-code",
            title="优化 agent UI 观感",
        ),
        state_source="loaded",
    )
    monkeypatch.setattr("voice_code.tui.resume_session", lambda session_id: resumed)
    monkeypatch.setattr(
        "voice_code.tui.get_or_create_service",
        lambda session_id, event_loop: f"service:{session_id}",
    )
    monkeypatch.setattr(
        "voice_code.tui.asyncio.get_running_loop",
        lambda: SimpleNamespace(),
    )

    assert screen._resume_into_current_view("session-xyz") is True
    assert screen._cwd == "/Users/example/workspace/voice-code"
    assert "cwd: /Users/example/workspace/voice-code" in screen.sub_title
    assert meta_messages[-1] == (
        "已恢复 session session-xyz  ·  标题：优化 agent UI 观感  ·  项目：voice-code"
    )


def test_resume_into_current_view_restores_subagent_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
):
    screen = AgentScreen()
    screen._turns = []
    screen._current_turn = None
    screen._current_turn_has_live_thinking = False
    screen._sticky_follow = False
    screen._pending_paste_text = ""
    screen._showing_paste_summary = False
    screen._model = SimpleNamespace(model_name="voice-code-pro")
    screen._cwd = "/Users/current/workspace"
    screen._runtime_state = None
    screen._last_persisted_runtime_signature = ""
    screen._transcript_writer = SimpleNamespace(close=lambda: None, file_path="/tmp/session.jsonl")
    screen._messages_to_turns = lambda messages: ["rebuilt"]  # type: ignore[method-assign]
    screen._refresh_history = lambda: None  # type: ignore[method-assign]
    screen._update_statusbar = lambda *args: None  # type: ignore[method-assign]
    screen._update_session_sidebar = lambda: None  # type: ignore[method-assign]
    screen._update_composer_meta = lambda *args: None  # type: ignore[method-assign]
    screen._append_system_info = lambda *args: None  # type: ignore[method-assign]
    screen.query_one = lambda *_args, **_kwargs: SimpleNamespace(focus=lambda: None)  # type: ignore[method-assign]
    screen._persist_session_runtime_state = lambda **_kwargs: None  # type: ignore[method-assign]

    task_state = {
        "tasks": [
            {
                "task_id": "task-1",
                "status": "completed",
                "transcript_path": "/tmp/task-1.jsonl",
            }
        ],
        "selected_task_id": "task-1",
    }
    resumed = ResumeSessionResult(
        session_id="session-xyz",
        messages=[HumanMessage(content="hello")],
        transcript_writer=SimpleNamespace(),
        runtime_state=build_session_runtime_state(
            session_id="session-xyz",
            cwd="/Users/example/workspace/voice-code",
            title="优化 agent UI 观感",
        ),
        state_source="loaded",
    )
    resumed.runtime_state.task_state = task_state
    resumed.runtime_state.subagent_notifications = [{"task_id": "task-1"}]

    class DummyService:
        def __init__(self) -> None:
            self.restored = None

        def has_runtime_state(self) -> bool:
            return False

        def restore_runtime_state(self, *, task_state, notifications) -> None:
            self.restored = {"task_state": task_state, "notifications": notifications}

        def get_task_transcript_path(self, task_id: str) -> str | None:
            return "/tmp/task-1.jsonl" if task_id == "task-1" else None

    dummy_service = DummyService()
    monkeypatch.setattr("voice_code.tui.resume_session", lambda session_id: resumed)
    monkeypatch.setattr(
        "voice_code.tui.get_or_create_service",
        lambda session_id, event_loop: dummy_service,
    )
    monkeypatch.setattr(
        "voice_code.tui.asyncio.get_running_loop",
        lambda: SimpleNamespace(),
    )

    assert screen._resume_into_current_view("session-xyz") is True
    assert screen._selected_task_id == "task-1"
    assert dummy_service.restored == {
        "task_state": task_state,
        "notifications": [{"task_id": "task-1"}],
    }


def test_resume_into_current_view_restores_ui_and_compact_state(
    monkeypatch: pytest.MonkeyPatch,
):
    screen = AgentScreen()
    screen._turns = []
    screen._current_turn = None
    screen._current_turn_has_live_thinking = False
    screen._sticky_follow = True
    screen._pending_paste_text = ""
    screen._showing_paste_summary = False
    screen._model = SimpleNamespace(model_name="voice-code-pro")
    screen._cwd = "/Users/current/workspace"
    screen._runtime_state = None
    screen._last_persisted_runtime_signature = ""
    screen._transcript_writer = SimpleNamespace(close=lambda: None, file_path="/tmp/session.jsonl")
    screen._messages_to_turns = lambda messages: [  # type: ignore[method-assign]
        SimpleNamespace(
            turn_id=1,
            ui_id=1,
            user_input="inspect",
            status="completed",
            entries=[
                SimpleNamespace(
                    kind="tool_pair",
                    tool_name="read",
                    tool_call_id="tool-1",
                    tool_result="line 1",
                    tool_result_preview="line 1",
                    is_result_collapsed=False,
                    is_result_manual=False,
                )
            ],
        )
    ]
    screen._refresh_history = lambda: None  # type: ignore[method-assign]
    screen._update_statusbar = lambda *args: None  # type: ignore[method-assign]
    screen._update_session_sidebar = lambda: None  # type: ignore[method-assign]
    screen._update_composer_meta = lambda *args: None  # type: ignore[method-assign]
    screen._append_system_info = lambda *args: None  # type: ignore[method-assign]
    screen.query_one = lambda *_args, **_kwargs: SimpleNamespace(focus=lambda: None)  # type: ignore[method-assign]
    screen._persist_session_runtime_state = lambda **_kwargs: None  # type: ignore[method-assign]

    resumed = ResumeSessionResult(
        session_id="session-xyz",
        messages=[HumanMessage(content="inspect")],
        transcript_writer=SimpleNamespace(),
        runtime_state=build_session_runtime_state(
            session_id="session-xyz",
            cwd="/Users/example/workspace/voice-code",
            title="恢复 UI 状态",
        ),
        state_source="loaded",
    )
    resumed.runtime_state.compact_state = {"compact_count": 3}
    resumed.runtime_state.ui_state = {
        "sticky_follow": False,
        "fallback_count": 2,
        "resume_count": 1,
        "tool_results": {"tool-1": {"collapsed": True, "manual": True}},
    }
    monkeypatch.setattr("voice_code.tui.resume_session", lambda session_id: resumed)
    monkeypatch.setattr(
        "voice_code.tui.get_or_create_service",
        lambda session_id, event_loop: "service:session-xyz",
    )
    monkeypatch.setattr(
        "voice_code.tui.asyncio.get_running_loop",
        lambda: SimpleNamespace(),
    )

    assert screen._resume_into_current_view("session-xyz") is True
    assert screen._sticky_follow is False
    assert screen._metric_compact_count == 3
    assert screen._metric_fallback_count == 2
    assert screen._metric_resume_count == 1
    tool_entry = screen._turns[0].entries[0]
    assert tool_entry.is_result_collapsed is True
    assert tool_entry.is_result_manual is True


def test_build_runtime_state_snapshot_includes_tool_ui_and_compact_metrics():
    screen = AgentScreen()
    screen._session_id = "session-xyz"
    screen._cwd = "/Users/example/workspace/voice-code"
    screen._runtime_state = build_session_runtime_state(
        session_id="session-xyz",
        cwd="/Users/example/workspace/voice-code",
    )
    screen._model = SimpleNamespace(model_name="voice-code-pro")
    screen._sticky_follow = False
    screen._metric_compact_count = 4
    screen._metric_fallback_count = 2
    screen._metric_resume_count = 1
    screen._selected_task_id = None
    screen._subagent_service = None
    screen._transcript_writer = None
    screen._turns = [
        SimpleNamespace(
            entries=[
                SimpleNamespace(
                    kind="tool_pair",
                    tool_call_id="tool-1",
                    tool_result="result",
                    is_result_collapsed=True,
                    is_result_manual=True,
                ),
                SimpleNamespace(
                    kind="tool_pair",
                    tool_call_id="tool-2",
                    tool_result="result",
                    is_result_collapsed=False,
                    is_result_manual=False,
                ),
            ]
        )
    ]

    state = screen._build_runtime_state_snapshot()

    assert state is not None
    assert state.compact_state["compact_count"] == 4
    assert state.ui_state["sticky_follow"] is False
    assert state.ui_state["fallback_count"] == 2
    assert state.ui_state["resume_count"] == 1
    assert state.ui_state["tool_results"] == {
        "tool-1": {"collapsed": True, "manual": True},
    }


def test_on_session_selected_blocks_while_busy():
    screen = AgentScreen()
    screen._is_busy = True
    messages: list[str] = []
    screen._append_system_info = lambda text: messages.append(text)  # type: ignore[method-assign]
    screen._resume_into_current_view = lambda session_id: True  # type: ignore[method-assign]

    screen.on_session_selected(SessionSelected("session-1"))

    assert messages == ["当前任务执行中，暂不支持切换会话。"]


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


def test_build_task_display_summarizes_subagent_states():
    tasks = [
        AgentTask(
            task_id="1",
            session_id="s",
            parent_task_id=None,
            parent_session_id=None,
            agent_type="researcher",
            description="a",
            prompt="a",
            status=TaskStatus.RUNNING,
            model_name=None,
            transcript_path="/tmp/1",
            created_at=1.0,
        ),
        AgentTask(
            task_id="2",
            session_id="s",
            parent_task_id=None,
            parent_session_id=None,
            agent_type="reviewer",
            description="b",
            prompt="b",
            status=TaskStatus.COMPLETED,
            model_name=None,
            transcript_path="/tmp/2",
            created_at=2.0,
        ),
        AgentTask(
            task_id="3",
            session_id="s",
            parent_task_id=None,
            parent_session_id=None,
            agent_type="fork",
            description="c",
            prompt="c",
            status=TaskStatus.FAILED,
            model_name=None,
            transcript_path="/tmp/3",
            created_at=3.0,
        ),
    ]

    display = build_task_display(tasks)

    assert display.running == 1
    assert display.completed == 1
    assert display.failed == 1
    assert display.cancelled == 0
    assert display.total == 3


def test_subagent_task_hint_prefers_running_then_failed_then_completed():
    screen = AgentScreen()
    screen._subagent_service = None
    assert screen._subagent_task_hint() == ""

    class DummyService:
        def __init__(self, tasks) -> None:
            self._tasks = tasks

        def list_tasks(self):
            return self._tasks

    running = AgentTask(
        task_id="1",
        session_id="s",
        parent_task_id=None,
        parent_session_id=None,
        agent_type="researcher",
        description="a",
        prompt="a",
        status=TaskStatus.RUNNING,
        model_name=None,
        transcript_path="/tmp/1",
        created_at=1.0,
    )
    failed = AgentTask(
        task_id="2",
        session_id="s",
        parent_task_id=None,
        parent_session_id=None,
        agent_type="reviewer",
        description="b",
        prompt="b",
        status=TaskStatus.FAILED,
        model_name=None,
        transcript_path="/tmp/2",
        created_at=2.0,
    )
    completed = AgentTask(
        task_id="3",
        session_id="s",
        parent_task_id=None,
        parent_session_id=None,
        agent_type="fork",
        description="c",
        prompt="c",
        status=TaskStatus.COMPLETED,
        model_name=None,
        transcript_path="/tmp/3",
        created_at=3.0,
    )

    screen._subagent_service = DummyService([running])  # type: ignore[assignment]
    assert screen._subagent_task_hint() == "1 个子 agent 运行中"

    screen._subagent_service = DummyService([failed])  # type: ignore[assignment]
    assert screen._subagent_task_hint() == "1 个子 agent 失败，可用 /tasks 查看"

    screen._subagent_service = DummyService([completed])  # type: ignore[assignment]
    assert screen._subagent_task_hint() == "1 个子 agent 已完成"


def test_task_overview_lines_marks_selected_task():
    screen = AgentScreen()
    screen._selected_task_id = "task-2"

    class DummyService:
        def list_tasks(self):
            return [
                AgentTask(
                    task_id="task-1",
                    session_id="s",
                    parent_task_id=None,
                    parent_session_id=None,
                    agent_type="researcher",
                    description="collect context",
                    prompt="a",
                    status=TaskStatus.RUNNING,
                    model_name=None,
                    transcript_path="/tmp/1",
                    created_at=1.0,
                ),
                AgentTask(
                    task_id="task-2",
                    session_id="s",
                    parent_task_id=None,
                    parent_session_id=None,
                    agent_type="reviewer",
                    description="check risks",
                    prompt="b",
                    status=TaskStatus.COMPLETED,
                    model_name=None,
                    transcript_path="/tmp/2",
                    created_at=2.0,
                ),
            ]

    screen._subagent_service = DummyService()  # type: ignore[assignment]

    lines = screen._task_overview_lines()

    assert lines == [
        "  task-1 [running] researcher - collect context",
        "* task-2 [completed] reviewer - check risks",
    ]


def test_selected_task_detail_text_includes_transcript_preview():
    screen = AgentScreen()
    screen._selected_task_id = "task-9"

    class DummyService:
        def get_task_text(self, task_id: str) -> str:
            assert task_id == "task-9"
            return "task_id: task-9\nstatus: completed"

        def get_task_transcript_text(
            self,
            task_id: str,
            *,
            max_chars: int | None = 5000,
        ) -> str:
            assert task_id == "task-9"
            assert max_chars is None
            return "[assistant]\ndone"

    screen._subagent_service = DummyService()  # type: ignore[assignment]

    detail = screen._selected_task_detail_text()

    assert "task_id: task-9" in detail
    assert "---- transcript ----" in detail
    assert "[assistant]\ndone" in detail


def test_selected_task_transcript_helpers_use_selected_task():
    screen = AgentScreen()
    screen._selected_task_id = "task-7"

    class DummyService:
        def get_task_transcript_text(
            self,
            task_id: str,
            *,
            max_chars: int | None = 5000,
        ) -> str:
            assert task_id == "task-7"
            assert max_chars is None
            return "[assistant]\nfinished"

        def get_task_transcript_path(self, task_id: str) -> str | None:
            assert task_id == "task-7"
            return "/tmp/task-7.jsonl"

    screen._subagent_service = DummyService()  # type: ignore[assignment]

    assert screen._selected_task_transcript_text() == "[assistant]\nfinished"
    assert screen._selected_task_transcript_path() == "/tmp/task-7.jsonl"
