"""Textual TUI — transcript-style chat layout."""

from __future__ import annotations

import argparse
import asyncio
import os
import queue
import subprocess
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from rich.columns import Columns
from rich.panel import Panel
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult, Screen
from textual.containers import Vertical, VerticalScroll
from textual.events import Paste
from textual.message import Message
from textual.widgets import Header, Static, TextArea

from voice_code.agent.abort import AbortSignal
from voice_code.agent.loop import agent_loop
from voice_code.agent.types import AgentEvent, EventType
from voice_code.clipboard import copy_to_clipboard
from voice_code.commands import (
    COMMON_HELP,
    TUI_EXTRA_HELP,
    format_session_lines,
    parse_command,
    resume_session,
)
from voice_code.llm.models import list_model_profiles
from voice_code.permissions import PermissionBehavior, PermissionDecision
from voice_code.runtime import bootstrap_runtime
from voice_code.session import TranscriptWriter
from voice_code.transcript_view import (
    PlainTurn,
    last_assistant_text,
    render_turn_as_plain_text,
    render_turns_as_plain_text,
)
from voice_code.tui_message_tree import (
    TuiEntry,
    append_turn_tool_call,
    build_tui_rows,
    collapse_turn_tool_results,
    find_last_thinking_row_id,
    find_latest_bash_tool_call_id,
    set_latest_bash_result_expanded,
    set_turn_streaming_text,
    set_turn_streaming_thinking,
    set_turn_tool_result,
    set_turn_tool_result_collapsed,
)
from voice_code.tui_message_widgets import TuiTurnWidget, to_plain_turn
from voice_code.tui_permission_dialog import PendingPermissionRequest, PermissionDialog
from voice_code.tui_permissions import make_tui_permission_context
from voice_code.tui_query_events import (
    apply_nonstream_event,
    apply_reasoning_event,
    apply_text_event,
    commit_streaming_buffer,
)
from voice_code.tui_runtime import QueryRuntimeState, build_runtime_display

STATUS_PANEL_BG = "#0f141a"
STATUS_PANEL_BORDER = "#2a3440"


@dataclass
class TurnBlock:
    """一轮对话的完整块模型。"""

    turn_id: int
    user_input: str
    ui_id: int = 0
    entries: list[TuiEntry] = field(default_factory=list)
    status: str = "completed"


TurnEntry = TuiEntry


class _NullWidget:
    def update(self, *_args: Any, **_kwargs: Any) -> None:
        return


TurnWidget = TuiTurnWidget


def _stream_turn_events_sync(
    text: str,
    tools: list,
    prompt: str,
    model: ChatOpenAI,
    fallback_model: ChatOpenAI | None,
    permission_context,
    resume_messages: list[BaseMessage] | None,
    transcript_writer: TranscriptWriter | None,
    abort_signal: AbortSignal | None,
    event_queue: queue.Queue[AgentEvent | Exception | None],
) -> None:
    async def _run() -> None:
        async for event in agent_loop(
            text,
            tools,
            prompt,
            model,
            permission_context=permission_context,
            abort_signal=abort_signal,
            resume_messages=resume_messages,
            transcript_writer=transcript_writer,
            fallback_model=fallback_model,
        ):
            event_queue.put(event)

    try:
        asyncio.run(_run())
    except Exception as ex:
        event_queue.put(ex)
    finally:
        event_queue.put(None)


class PromptTextArea(TextArea):
    def action_submit(self) -> None:
        self.post_message(self.SubmitRequested())

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.action_submit()
            return
        await super()._on_key(event)

    class SubmitRequested(Message):
        """请求提交当前输入。"""


class AgentScreen(Screen):
    AUTO_FOCUS = "Input"

    BINDINGS = [
        ("ctrl+c", "interrupt_turn", "Interrupt turn"),
        ("ctrl+y", "copy_last_reply", "Copy last reply"),
        ("ctrl+shift+y", "copy_last_turn", "Copy last turn"),
        ("ctrl+shift+c", "copy_history", "Copy history"),
    ]

    def __init__(self, profile: str | None = None) -> None:
        super().__init__()
        self._profile = profile
        self._model: ChatOpenAI | None = None
        self._fallback_model: ChatOpenAI | None = None
        self._tools: list[Any] = []
        self._transcript_writer: TranscriptWriter | None = None
        self._abort_signal = AbortSignal()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, icon="⚡")
        with VerticalScroll(id="messages"):
            yield Vertical(id="history")
        yield Static("", id="statusbar")
        yield PromptTextArea(
            "",
            id="input",
            soft_wrap=True,
            show_line_numbers=False,
            compact=True,
            placeholder="Enter send · pasted text shows char count · Ctrl+D quit · /help",
        )

    def on_mount(self) -> None:
        self._perm_ctx = make_tui_permission_context(self._submit_permission_request)
        self._is_busy = False
        self._model = None
        self._fallback_model = None
        self._tools = []
        self._fallback_profile = None
        self._prompt = ""
        self._session_id = "initializing"
        self._transcript_writer = None
        self._turns: list[TurnBlock] = []
        self._current_turn: TurnBlock | None = None
        self._turn_counter = 0
        self._resume_messages: list[BaseMessage] | None = None
        self._metric_output_chars = 0
        self._metric_compact_count = 0
        self._metric_fallback_count = 0
        self._metric_resume_count = 0
        self._pending_paste_text = ""
        self._showing_paste_summary = False
        self._history_refresh_running = False
        self._history_refresh_requested = False
        self._sticky_follow = True
        self._current_turn_has_live_thinking = False

        async def _init() -> None:
            runtime = await bootstrap_runtime(profile=self._profile)
            self._tools = runtime.tools
            self._model = runtime.model
            self._fallback_model = runtime.fallback_model
            self._fallback_profile = runtime.fallback_profile
            self._prompt = runtime.prompt
            self._session_id = runtime.session_id
            self._transcript_writer = runtime.transcript_writer
            self.app.title = "reasoning"
            self.sub_title = (
                f"model: {runtime.model.model_name}"
                f"  cwd: {runtime.cwd}"
                f"  session: {self._session_id}"
            )
            self._update_statusbar()
            self._append_system_info(f"model: {runtime.model.model_name}")
            self._append_system_info(f"tools: {', '.join(t.name for t in self._tools)}")
            self._append_system_info("Ready.")

        asyncio.create_task(_init())

    def _submit_permission_request(self, pending: PendingPermissionRequest) -> None:
        """Receive a permission request from the agent worker thread."""
        try:
            self.app.call_from_thread(self._open_permission_dialog, pending)
        except Exception:
            pending.resolve(
                PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message="Permission denied: TUI permission dialog is unavailable.",
                )
            )

    def _open_permission_dialog(self, pending: PendingPermissionRequest) -> None:
        self._append_info_to_current_turn(
            f"Permission requested: {pending.request.tool_name}"
        )

        def on_done(decision: PermissionDecision | None) -> None:
            final = decision or PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="Permission request was dismissed.",
            )
            pending.resolve(final)
            if final.behavior == PermissionBehavior.ALLOW:
                self._append_info_to_current_turn(
                    f"Permission allowed: {pending.request.tool_name}"
                )
            else:
                self._append_error_to_current_turn(
                    final.message or f"Permission denied: {pending.request.tool_name}"
                )

        self.app.push_screen(PermissionDialog(pending.request), callback=on_done)

    def _statusbar_widget(self) -> Static:
        return self.query_one("#statusbar", Static)

    def _messages_widget(self) -> VerticalScroll:
        return self.query_one("#messages", VerticalScroll)

    def _history_widget(self) -> Vertical:
        return self.query_one("#history", Vertical)

    def _update_statusbar(self, phase: str = "", tool_count: int = 0) -> None:
        phase_label = phase or ("busy" if self._is_busy else "idle")
        approx_tokens = max(0, self._metric_output_chars // 4)
        model_name = self._model.model_name if self._model is not None else "(loading)"
        left = Text()
        left.append("model ", style="dim")
        left.append(model_name, style="bold #8ab4f8")
        left.append("  session ", style="dim")
        left.append(self._session_id, style="bold #c9d1d9")
        left.append("  phase ", style="dim")
        left.append(phase_label, style="bold #e6b450")
        if tool_count:
            left.append("  tools ", style="dim")
            left.append(str(tool_count), style="bold #7ee787")

        right = Text(justify="right")
        right.append("out ", style="dim")
        right.append(f"~{approx_tokens} tok", style="bold #c9d1d9")
        right.append("  compact ", style="dim")
        right.append(str(self._metric_compact_count), style="bold #7ee787")
        right.append("  fallback ", style="dim")
        right.append(str(self._metric_fallback_count), style="bold #ffb86b")
        right.append("  resume ", style="dim")
        right.append(str(self._metric_resume_count), style="bold #8ab4f8")

        self._statusbar_widget().update(
            Panel(
                Columns([left, right], expand=True, equal=False),
                border_style=STATUS_PANEL_BORDER,
                style=f"on {STATUS_PANEL_BG}",
                padding=(0, 1),
            )
        )

    def _render_thinking_block(self, text: str) -> Text:
        rendered = Text()
        rendered.append("∴ Thinking…\n", style="dim italic")
        rendered.append(text, style="dim")
        return rendered

    def _is_near_bottom(self) -> bool:
        widget = self._messages_widget()
        max_y = getattr(widget, "max_scroll_y", 0)
        scroll_y = getattr(widget, "scroll_y", 0)
        return max_y - scroll_y <= 2

    def _scroll_to_bottom(self, *, force: bool = False) -> None:
        if force or self._sticky_follow or self._is_near_bottom():
            self._messages_widget().scroll_end(animate=False)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._sticky_follow = False

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        widget = self._messages_widget()
        if getattr(widget, "scroll_y", 0) >= getattr(widget, "max_scroll_y", 0) - 2:
            self._sticky_follow = True

    def on_key(self, event: events.Key) -> None:
        if event.key in {"end", "ctrl+end"}:
            self._sticky_follow = True
            self._scroll_to_bottom(force=True)
        elif event.key in {"pageup", "up"}:
            self._sticky_follow = False
        elif event.key in {"pagedown", "down"}:
            if self._is_near_bottom():
                self._sticky_follow = True

    def _refresh_history(self) -> None:
        self._history_refresh_requested = True
        if self._history_refresh_running:
            return
        self._history_refresh_running = True
        self._refresh_history_async()

    @work(group="history-refresh")
    async def _refresh_history_async(self) -> None:
        try:
            while self._history_refresh_requested:
                self._history_refresh_requested = False
                should_follow = self._is_near_bottom()
                container = self._history_widget()
                turns = list(self._turns)
                if self._current_turn is not None:
                    turns.append(self._current_turn)
                latest_bash_tool_call_id = find_latest_bash_tool_call_id(
                    self._turns,
                    self._current_turn,
                )
                set_latest_bash_result_expanded(turns, latest_bash_tool_call_id)
                hide_past_thinking = self._current_turn_has_live_thinking
                last_visible_thinking_row_id = find_last_thinking_row_id(
                    self._turns,
                    self._current_turn,
                )

                desired_ids: list[str] = []
                existing_widgets = {
                    widget.id: widget for widget in container.query(TurnWidget)
                }

                if not existing_widgets and turns:
                    await container.remove_children()

                async with container.batch():
                    for turn in turns:
                        if not turn.entries and not turn.user_input:
                            continue
                        if not turn.ui_id:
                            turn.ui_id = turn.turn_id
                        widget_id = f"turn-{turn.ui_id}"
                        desired_ids.append(widget_id)
                        widget = existing_widgets.get(widget_id)
                        if widget is None:
                            widget = TurnWidget(turn, id=widget_id)
                            await container.mount(widget)
                        await widget.sync_turn(
                            turn,
                            hide_past_thinking=hide_past_thinking,
                            last_visible_thinking_row_id=last_visible_thinking_row_id,
                        )

                    for widget_id, widget in existing_widgets.items():
                        if widget_id not in desired_ids:
                            await widget.remove()

                self._scroll_to_bottom(force=should_follow)
        finally:
            self._history_refresh_running = False

    def _start_turn(self, user_input: str) -> None:
        self._turn_counter += 1
        self._current_turn = TurnBlock(
            turn_id=self._turn_counter,
            ui_id=self._turn_counter,
            user_input=user_input,
            status="streaming",
        )
        self._current_turn_has_live_thinking = False
        self._refresh_history()

    def _append_system_info(self, text: str) -> None:
        for turn in self._turns:
            if turn.turn_id == 0:
                turn.entries.append(TuiEntry(kind="info", text=text))
                self._refresh_history()
                return
        self._turns.append(
            TurnBlock(
                turn_id=0,
                ui_id=0,
                user_input="",
                entries=[TuiEntry(kind="info", text=text)],
                status="completed",
            )
        )
        self._refresh_history()

    def _append_system_error(self, text: str) -> None:
        for turn in self._turns:
            if turn.turn_id == 0:
                turn.entries.append(TuiEntry(kind="error", text=text))
                self._refresh_history()
                return
        self._turns.append(
            TurnBlock(
                turn_id=0,
                ui_id=0,
                user_input="",
                entries=[TuiEntry(kind="error", text=text)],
                status="completed",
            )
        )
        self._refresh_history()

    def _append_info_to_current_turn(self, text: str) -> None:
        if self._current_turn is not None:
            self._current_turn.entries.append(TuiEntry(kind="info", text=text))
            self._refresh_history()
            return
        self._append_system_info(text)

    def _append_error_to_current_turn(self, text: str) -> None:
        if self._current_turn is not None:
            self._current_turn.entries.append(TuiEntry(kind="error", text=text))
            self._refresh_history()
            return
        self._append_system_error(text)

    def _set_streaming_preview(self, text: str) -> None:
        if not self._current_turn:
            return
        if set_turn_streaming_text(self._current_turn, text):
            self._refresh_history()

    def _set_streaming_preview_final(self, text: str) -> None:
        if not self._current_turn:
            return
        if set_turn_streaming_text(self._current_turn, text, is_live=False):
            self._refresh_history()

    def _set_streaming_thinking(self, text: str, *, is_live: bool = True) -> None:
        if not self._current_turn:
            return
        if set_turn_streaming_thinking(self._current_turn, text, is_live=is_live):
            self._refresh_history()

    def _append_status_to_current_turn(self, text: str) -> None:
        self._append_info_to_current_turn(text)

    def _append_tool_error(self, event: AgentEvent) -> None:
        if not self._current_turn or not event.tool_call_id:
            self._append_error_to_current_turn(event.content)
            return
        changed = set_turn_tool_result(
            self._current_turn,
            tool_name=event.tool_name,
            tool_call_id=event.tool_call_id,
            tool_result=f"<tool_use_error>{event.content}</tool_use_error>",
            tool_result_preview=event.content[:200].replace("\n", " "),
            is_result_collapsed=True,
        )
        if changed:
            self._refresh_history()

    def _route_error_event(self, event: AgentEvent) -> None:
        if event.status in {"compact", "fallback", "resume"}:
            self._append_status_to_current_turn(event.content)
            return
        if event.status in {"permission_denied", "generic_error"} and event.tool_call_id:
            self._append_tool_error(event)
            return
        self._append_error_to_current_turn(event.content)

    def _append_tool_call(self, event: AgentEvent) -> None:
        if not self._current_turn:
            return
        append_turn_tool_call(
            self._current_turn,
            tool_name=event.tool_name,
            tool_call_id=event.tool_call_id,
            tool_args=dict(event.tool_args),
        )
        self._refresh_history()

    def _append_tool_result(self, event: AgentEvent) -> None:
        if not self._current_turn:
            return
        changed = set_turn_tool_result(
            self._current_turn,
            tool_name=event.tool_name,
            tool_call_id=event.tool_call_id,
            tool_result=event.content,
            tool_result_preview=event.content[:200].replace("\n", " "),
            is_result_collapsed=True,
        )
        if changed:
            self._refresh_history()

    def _finish_turn(self) -> None:
        if not self._current_turn:
            return
        self._current_turn.status = "completed"
        self._turns.append(self._current_turn)
        self._current_turn = None
        self._current_turn_has_live_thinking = False
        self._sticky_follow = True
        self._refresh_history()

    def _clear_live_regions(self) -> None:
        self._update_statusbar()
        self._scroll_to_bottom()

    def _copy_text(self, text: str, success_message: str) -> None:
        if not text.strip():
            self._append_system_info("Nothing to copy.")
            return
        try:
            self.app.copy_to_clipboard(text)
            self._append_system_info(success_message)
            return
        except Exception:
            pass

        try:
            subprocess.run(
                ["pbcopy"],
                input=text,
                text=True,
                check=True,
            )
            self._append_system_info(success_message)
        except Exception as ex:
            self._append_system_error(f"Copy failed: {ex}")

    def _build_transcript_text(self) -> str:
        return render_turns_as_plain_text(self._plain_turns())

    def _last_assistant_text(self) -> str:
        return last_assistant_text(self._plain_turns())

    def _plain_turns(self) -> list[PlainTurn]:
        turns = list(self._turns)
        if self._current_turn is not None:
            turns.append(self._current_turn)
        hide_past_thinking = self._current_turn_has_live_thinking
        last_visible_thinking_row_id = find_last_thinking_row_id(
            self._turns,
            self._current_turn,
        )
        plain_turns: list[PlainTurn] = []
        for turn in turns:
            plain_turns.append(
                to_plain_turn(
                    turn,
                    hide_past_thinking=hide_past_thinking,
                    last_visible_thinking_row_id=last_visible_thinking_row_id,
                )
            )
        return plain_turns

    def _visible_row_texts(self) -> list[str]:
        rows = build_tui_rows(
            self._turns,
            self._current_turn,
            hide_past_thinking=self._current_turn_has_live_thinking,
            last_visible_thinking_row_id=find_last_thinking_row_id(
                self._turns,
                self._current_turn,
            ),
        )
        return [row.text for row in rows if row.kind == "assistant_text" and row.text.strip()]

    def _submit_input(self) -> None:
        input_widget = self.query_one("#input", TextArea)
        raw_text = self._pending_paste_text if self._showing_paste_summary else input_widget.text
        text = raw_text.strip()
        if not text:
            return

        input_widget.text = ""
        self._pending_paste_text = ""
        self._showing_paste_summary = False

        if text.startswith("/"):
            self._handle_command(text)
            return

        if self._is_busy:
            self._append_system_info("上一轮请求还没结束，请稍等。")
            return

        self._start_turn(text)
        input_widget.disabled = True
        self._is_busy = True
        self._abort_signal.clear()
        self._query(text)

    def on_prompt_text_area_submit_requested(self, _: PromptTextArea.SubmitRequested) -> None:
        self._submit_input()

    def on_paste(self, event: Paste) -> None:
        if self.focused is not self.query_one("#input", TextArea):
            return
        pasted = event.text
        if not pasted:
            return
        if len(pasted) <= 80 and "\n" not in pasted:
            self._pending_paste_text = ""
            self._showing_paste_summary = False
            return

        event.prevent_default()
        event.stop()
        self._pending_paste_text = pasted
        self._showing_paste_summary = True
        input_widget = self.query_one("#input", TextArea)
        input_widget.text = f"[pasted {len(pasted)} chars]"

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "input":
            return
        summary = f"[pasted {len(self._pending_paste_text)} chars]"
        if self._showing_paste_summary and event.text_area.text != summary:
            self._pending_paste_text = ""
            self._showing_paste_summary = False

    @work
    async def _query(self, text: str) -> None:
        state = QueryRuntimeState()

        def update_thinking() -> None:
            display = build_runtime_display(state)
            self._current_turn_has_live_thinking = bool(state.live_thinking_text.strip())
            if state.live_thinking_text.strip():
                self._set_streaming_thinking(state.live_thinking_text, is_live=True)
            else:
                self._set_streaming_thinking("")
            self._update_statusbar(display.status_phase, display.tool_count)

        def flush_pending_buffer(buffer: str) -> str:
            commit = commit_streaming_buffer(state, buffer)
            if not buffer:
                self._set_streaming_preview("")
                self._set_streaming_thinking("")
                return ""
            if commit.thinking_text:
                self._set_streaming_thinking(commit.thinking_text, is_live=False)
            else:
                self._set_streaming_thinking("")
            if commit.visible_text:
                self._set_streaming_preview_final(commit.visible_text)
            else:
                self._set_streaming_preview("")
            update_thinking()
            return ""

        def apply_nonstream_outcome(event: AgentEvent) -> None:
            outcome = apply_nonstream_event(state, event)
            if outcome.kind == "tool_call":
                update_thinking()
                self._append_tool_call(event)
            elif outcome.kind == "tool_result":
                update_thinking()
                self._append_tool_result(event)
            elif outcome.kind == "error":
                self._apply_error_event_metrics(event)
                update_thinking()
                self._route_error_event(event)
            elif outcome.kind == "finish":
                update_thinking()
                self._finish_turn()
                self._scroll_to_bottom(force=True)

        try:
            if self._model is None:
                self._append_error_to_current_turn("Error: model is still initializing")
                self._finish_turn()
                return

            model = self._model
            fallback_model = self._fallback_model
            event_queue: queue.Queue[AgentEvent | Exception | None] = queue.Queue()
            producer = asyncio.create_task(
                asyncio.to_thread(
                    _stream_turn_events_sync,
                    text,
                    self._tools,
                    self._prompt,
                    model,
                    fallback_model,
                    self._perm_ctx,
                    self._resume_messages,
                    self._transcript_writer,
                    self._abort_signal,
                    event_queue,
                )
            )

            pending_text = ""
            self._update_statusbar("streaming", 0)

            while True:
                item = await asyncio.to_thread(event_queue.get)
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item

                event = item
                if event.phase:
                    state.current_phase = event.phase
                    update_thinking()
                if event.type == EventType.TEXT:
                    text_update = apply_text_event(
                        state,
                        pending_text=pending_text,
                        content=event.content,
                    )
                    pending_text = text_update.pending_text
                    self._metric_output_chars += text_update.output_chars
                    should_follow = self._is_near_bottom()
                    self._set_streaming_preview(text_update.visible_text)
                    update_thinking()
                    self._update_statusbar(
                        state.current_phase or "streaming",
                        len(state.executing_tools),
                    )
                    self._scroll_to_bottom(force=should_follow)
                    continue
                if event.type == EventType.REASONING:
                    apply_reasoning_event(state, event.content)
                    update_thinking()
                    self._scroll_to_bottom()
                    continue

                pending_text = flush_pending_buffer(pending_text)
                apply_nonstream_outcome(event)

            pending_text = flush_pending_buffer(pending_text)
            await producer
            if self._transcript_writer is not None:
                self._resume_messages = self._transcript_writer.read_all_messages()
        except Exception as ex:
            state.executing_tools.clear()
            state.current_phase = ""
            state.live_thinking_text = ""
            state.streaming_text = ""
            state.streaming_thinking = ""
            update_thinking()
            self._append_error_to_current_turn(f"Error: {ex}")
            self._finish_turn()
            if self._transcript_writer is not None:
                self._resume_messages = self._transcript_writer.read_all_messages()
        finally:
            self._current_turn_has_live_thinking = False
            self._clear_live_regions()
            self._is_busy = False
            input_widget = self.query_one("#input", TextArea)
            input_widget.disabled = False
            input_widget.focus()

    # ── Clipboard actions ──────────────────────────────────────────────

    def action_copy_last_reply(self) -> None:
        """Copy last agent reply text to clipboard."""
        text = self._get_last_agent_text()
        if text and copy_to_clipboard(text):
            self._notify_clipboard("Copied reply to clipboard")

    def action_interrupt_turn(self) -> None:
        """Interrupt the current turn in the interactive TUI."""
        if not self._is_busy:
            self._append_system_info("No running turn to interrupt.")
            return
        self._abort_signal.trigger()
        self._append_info_to_current_turn("Interrupt requested…")

    def action_copy_last_turn(self) -> None:
        """Copy last turn (user + agent + tools) to clipboard."""
        turn = self._get_last_turn()
        if turn is None:
            return
        text = render_turn_as_plain_text(self._to_plain_turn(turn))
        if text and copy_to_clipboard(text):
            self._notify_clipboard(f"Copied turn ({len(text)} chars)")

    def action_copy_history(self) -> None:
        """Copy all visible turns to clipboard."""
        all_text = render_turns_as_plain_text(self._plain_turns())
        if all_text and copy_to_clipboard(all_text):
            self._notify_clipboard(f"Copied history ({len(all_text)} chars)")

    def _get_last_agent_text(self) -> str:
        """Return the last agent text across all turns."""
        visible_texts = self._visible_row_texts()
        return visible_texts[-1] if visible_texts else ""

    def _get_last_turn(self) -> TurnBlock | None:
        """Return the most recent completed/streaming turn."""
        if self._current_turn is not None:
            return self._current_turn
        if self._turns:
            return self._turns[-1]
        return None

    def _to_plain_turn(self, turn: TurnBlock) -> PlainTurn:
        return to_plain_turn(
            turn,
            hide_past_thinking=self._current_turn_has_live_thinking,
            last_visible_thinking_row_id=find_last_thinking_row_id(
                self._turns,
                self._current_turn,
            ),
        )

    def _notify_clipboard(self, message: str) -> None:
        """Show clipboard status in statusbar for 2 seconds."""
        bar = self.query_one("#statusbar", Static)
        bar.update(Text(message, style="bold #7ee787"))  # type: ignore[arg-type]
        self.set_timer(2.0, lambda: self._update_statusbar())

    def _apply_error_event_metrics(self, event: AgentEvent) -> None:
        """Map error event semantics to TUI counters.

        优先用稳定语义字段 status，旧文案判断只做兼容兜底。
        """
        lowered = event.content.lower()
        is_compact = (
            event.status == "compact"
            or "compact:" in lowered
            or "conversation compacted" in lowered
        )
        if is_compact:
            self._metric_compact_count += 1
        if event.status == "fallback" or "fallback model" in lowered:
            self._metric_fallback_count += 1
        if event.status == "resume" or "resuming" in lowered or "max_output_tokens hit" in lowered:
            self._metric_resume_count += 1

    def _render_runtime_thinking_state(
        self,
        *,
        thinking_widget: Static,
        state: QueryRuntimeState,
    ) -> None:
        """Legacy hook kept for tests; message tree is the live render path."""
        display = build_runtime_display(state)
        if display.thinking_text:
            thinking_widget.update(self._render_thinking_block(display.thinking_text))
        else:
            thinking_widget.update(Text())
        self._update_statusbar(display.status_phase, display.tool_count)

    # ── Commands ───────────────────────────────────────────────────────

    def _handle_command(self, text: str) -> None:
        command = parse_command(text)
        if command.name == "help":
            self._append_system_info(COMMON_HELP + TUI_EXTRA_HELP)
        elif command.name == "clear":
            self._turns.clear()
            self._current_turn = None
            self._sticky_follow = True
            for child in list(self._history_widget().query(TurnWidget)):
                child.remove()
            self._clear_live_regions()
        elif command.name == "copy":
            self._copy_text(self._build_transcript_text(), "Transcript copied to clipboard.")
        elif command.name == "copylast":
            self._copy_text(self._last_assistant_text(), "Last assistant reply copied.")
        elif command.name == "tools":
            self._append_system_info(", ".join(t.name for t in self._tools))
        elif command.name == "perm":
            self._append_system_info(f"mode: {self._perm_ctx.mode}")
        elif command.name == "sessions":
            for line in format_session_lines(limit=10):
                self._append_system_info(line)
        elif command.name == "resume":
            session_id = str(command.args.get("session_id", "")).strip()
            if not session_id:
                self._append_system_error("Usage: /resume <session_id>")
                return
            try:
                resumed = resume_session(session_id)
            except FileNotFoundError:
                self._append_system_error(f"Session not found: {session_id}")
                return
            self._turns = self._messages_to_turns(resumed.messages)
            self._current_turn = None
            self._current_turn_has_live_thinking = False
            self._resume_messages = resumed.messages
            if self._transcript_writer is not None:
                self._transcript_writer.close()
            self._sticky_follow = True
            self._session_id = resumed.session_id
            self._transcript_writer = resumed.transcript_writer
            self._pending_paste_text = ""
            self._showing_paste_summary = False
            model_name = self._model.model_name if self._model is not None else "(loading)"
            self.sub_title = (
                f"model: {model_name}"
                f"  cwd: {os.getcwd()}"
                f"  session: {self._session_id}"
            )
            self._refresh_history()
            self._append_system_info(f"Resumed session: {resumed.session_id}")
        elif command.name == "detail":
            turn_id = command.args.get("turn_id")
            self._expand_detail(turn_id)
        elif command.name == "collapse":
            self._collapse_all()
        elif command.name == "quit":
            if self._transcript_writer is not None:
                self._transcript_writer.close()
            self.app.exit()
        else:
            self._append_system_error(f"Unknown: {command.args.get('command', text)}")

    def _expand_detail(self, turn_id: int | None) -> None:
        turns = self._turns
        if self._current_turn is not None:
            turns = turns + [self._current_turn]

        candidates = turns if turn_id is None else [t for t in turns if t.turn_id == turn_id]
        for turn in reversed(candidates):
            for entry in turn.entries:
                if entry.kind == "tool_pair" and entry.tool_result:
                    if set_turn_tool_result_collapsed(turn, entry.tool_call_id, False):
                        self._refresh_history()
                        return
                    self._refresh_history()
                    return
        self._append_system_info("No tool result to expand.")

    def _collapse_all(self) -> None:
        changed = False
        for turn in self._turns:
            before = any(
                entry.kind == "tool_pair" and not entry.is_result_collapsed
                for entry in turn.entries
            )
            collapse_turn_tool_results(turn)
            changed = changed or before
        if self._current_turn:
            before = any(
                entry.kind == "tool_pair" and not entry.is_result_collapsed
                for entry in self._current_turn.entries
            )
            collapse_turn_tool_results(self._current_turn)
            changed = changed or before
        if changed:
            self._refresh_history()

    def _messages_to_turns(self, messages: list[BaseMessage]) -> list[TurnBlock]:
        turns: list[TurnBlock] = []
        current: TurnBlock | None = None
        turn_counter = 0

        def _has_tool_pair(turn: TurnBlock, tool_call_id: str) -> bool:
            if not tool_call_id:
                return False
            return any(
                entry.kind == "tool_pair" and entry.tool_call_id == tool_call_id
                for entry in turn.entries
            )

        def _append_ai_content(turn: TurnBlock, content: Any) -> None:
            if isinstance(content, str):
                if content:
                    turn.entries.append(TuiEntry(kind="text", text=content))
                return
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type", "")
                    else:
                        block_type = getattr(block, "type", "")
                    if block_type == "text":
                        if isinstance(block, dict):
                            text = str(block.get("text", ""))
                        else:
                            text = getattr(block, "text", "")
                        if text:
                            turn.entries.append(TuiEntry(kind="text", text=text))
                    elif block_type in {"thinking", "redacted_thinking"}:
                        if isinstance(block, dict):
                            thinking = str(block.get("thinking", ""))
                        else:
                            thinking = getattr(block, "thinking", "")
                        if thinking:
                            turn.entries.append(TuiEntry(kind="reasoning", text=thinking))
                    elif block_type == "tool_use":
                        if isinstance(block, dict):
                            tool_name = str(block.get("name", ""))
                            tool_call_id = str(block.get("id", ""))
                            tool_args = dict(block.get("input", {}) or {})
                        else:
                            tool_name = getattr(block, "name", "")
                            tool_call_id = getattr(block, "id", "")
                            tool_args = dict(getattr(block, "input", {}) or {})
                        if not _has_tool_pair(turn, tool_call_id):
                            turn.entries.append(
                                TuiEntry(
                                    kind="tool_pair",
                                    tool_name=tool_name,
                                    tool_call_id=tool_call_id,
                                    tool_args=dict(tool_args or {}),
                                )
                            )
                return
            text = str(content)
            if text:
                turn.entries.append(TuiEntry(kind="text", text=text))

        def _append_tool_result(turn: TurnBlock, msg: ToolMessage) -> None:
            matched = False
            for entry in reversed(turn.entries):
                if entry.kind == "tool_pair" and entry.tool_call_id == msg.tool_call_id:
                    entry.tool_result = str(msg.content)
                    entry.tool_result_preview = str(msg.content)[:200].replace("\n", " ")
                    matched = True
                    break
            if not matched:
                turn.entries.append(
                    TuiEntry(
                        kind="tool_pair",
                        tool_name=getattr(msg, "name", "") or "",
                        tool_call_id=msg.tool_call_id,
                        tool_result=str(msg.content),
                        tool_result_preview=str(msg.content)[:200].replace("\n", " "),
                    )
                )

        for msg in messages:
            if isinstance(msg, SystemMessage):
                continue
            if isinstance(msg, HumanMessage):
                if isinstance(msg.content, str) and msg.content.startswith("[SNIP:"):
                    continue
                if current is not None:
                    turns.append(current)
                turn_counter += 1
                current = TurnBlock(
                    turn_id=turn_counter,
                    ui_id=turn_counter,
                    user_input=str(msg.content),
                    status="completed",
                )
                continue
            if current is None:
                continue
            if isinstance(msg, AIMessage):
                _append_ai_content(current, msg.content)
                for tc in getattr(msg, "tool_calls", []) or []:
                    if isinstance(tc, dict):
                        tool_call_id = str(tc.get("id", ""))
                        if not _has_tool_pair(current, tool_call_id):
                            current.entries.append(
                                TuiEntry(
                                    kind="tool_pair",
                                    tool_name=str(tc.get("name", "")),
                                    tool_call_id=tool_call_id,
                                    tool_args=dict(tc.get("args", {})),
                                )
                            )
                continue
            if isinstance(msg, ToolMessage):
                _append_tool_result(current, msg)

        if current is not None:
            turns.append(current)

        self._turn_counter = turn_counter
        return turns


class AgentApp(App):
    CSS = """
    Screen { background: #0d1117; }
    #messages {
        background: #0d1117;
        color: #e0e0e0;
        padding: 0 2;
    }
    #history, #thinking, #streaming {
        background: #0d1117;
        color: #e0e0e0;
        width: 100%;
    }
    #history {
        height: auto;
    }
    #statusbar {
        dock: bottom;
        background: #0d1117;
        color: #c9d1d9;
        margin: 0 2;
        height: 3;
    }
    #thinking {
        color: #8b949e;
        margin: 0 0 1 0;
    }
    #streaming {
        color: #e0e0e0;
        margin: 1 0 0 0;
    }
    TextArea {
        dock: bottom;
        margin: 0 2 1 2;
        background: #161b22;
        border: solid #30363d;
        color: #e0e0e0;
        height: 5;
    }
    TextArea:focus { border: solid #58a6ff; }
    Header { background: #161b22; color: #e0e0e0; text-style: bold; }
    """

    def __init__(self, profile: str | None = None) -> None:
        super().__init__()
        self._profile = profile

    def on_mount(self) -> None:
        self.push_screen(AgentScreen(profile=self._profile))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reasoning-tui", description="Textual TUI")
    parser.add_argument("--profile", default=None, help="Model profile name from models.toml")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        AgentApp(profile=args.profile).run()
    except ValueError as e:
        profiles = ", ".join(list_model_profiles()) or "(none)"
        raise SystemExit(f"{e}\nAvailable profiles: {profiles}") from e


if __name__ == "__main__":
    main()
