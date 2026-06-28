"""Textual widgets for the TUI message tree."""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Static

from reasoning_agent.clipboard import copy_to_clipboard
from reasoning_agent.transcript_view import PlainTurn, PlainTurnEntry, render_turn_as_plain_text
from reasoning_agent.tui_message_tree import (
    TuiRow,
    build_tui_rows_for_turn,
    set_turn_tool_result_collapsed,
)

TOOL_COLORS = {
    "bash": "bold #ff8800",
    "read": "bold #4488ff",
    "write": "bold #22cc66",
    "edit": "bold #ffcc00",
    "glob": "bold #22cccc",
    "grep": "bold #cc44ff",
    "todo_write": "bold #ff66aa",
    "ask_user_question": "bold #66aaff",
    "web_fetch": "bold #66ff88",
}

TEXT_DIM_STYLE = "dim"


def _clean_tool_use_error(text: str) -> str:
    return (
        text.replace("<tool_use_error>", "")
        .replace("</tool_use_error>", "")
        .strip()
    )


def _append_wrapped_lines(
    rendered: Text,
    text: str,
    *,
    first_prefix: str,
    rest_prefix: str,
    style: str,
) -> None:
    lines = text.splitlines() or [""]
    for index, line in enumerate(lines):
        prefix = first_prefix if index == 0 else rest_prefix
        rendered.append(f"{prefix}{line}\n", style=style)


def _inline_preview(text: str, limit: int) -> str:
    preview = text.strip().replace("\n", " ")
    if len(preview) <= limit:
        return preview
    return preview[:limit] + "…"


def render_tui_row(row: TuiRow) -> RenderableType:
    if row.kind == "system_info":
        text = Text()
        _append_wrapped_lines(
            text,
            row.text,
            first_prefix="ℹ ",
            rest_prefix="  ",
            style=TEXT_DIM_STYLE,
        )
        return text

    if row.kind == "system_error":
        text = Text()
        _append_wrapped_lines(
            text,
            row.text[:300],
            first_prefix="✗ ",
            rest_prefix="  ",
            style="bold red",
        )
        return text

    if row.kind == "user_input":
        text = Text()
        _append_wrapped_lines(
            text,
            row.text,
            first_prefix="❯ ",
            rest_prefix="  ",
            style="bold #00ff88",
        )
        return text

    if row.kind == "assistant_thinking":
        if not row.text.strip():
            return Text("∴ Thinking…", style="dim italic")
        return Group(
            Text("∴ Thinking…", style="dim italic"),
            Text(row.text, style="dim"),
        )

    if row.kind == "assistant_tool_use":
        args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in row.tool_args.items())
        title = Text()
        title.append("⚡ ", style=TOOL_COLORS.get(row.tool_name, "bold #ffaa00"))
        title.append(row.tool_name, style=TOOL_COLORS.get(row.tool_name, "bold #ffaa00"))
        if args:
            title.append(f"  {args}", style="dim")
        has_error = "<tool_use_error>" in row.tool_result
        has_result = bool(row.tool_result)
        is_expanded = has_result and not row.is_result_collapsed
        is_collapsed = has_result and row.is_result_collapsed
        is_running = not has_result and row.is_streaming
        if has_error:
            state_label = "  error"
        elif is_expanded:
            state_label = "  expanded"
        elif is_collapsed:
            state_label = "  collapsed"
        elif is_running:
            state_label = "  running"
        else:
            state_label = "  queued"
        title.append(state_label, style="dim")
        preview_text = ""
        if row.is_result_collapsed:
            if has_error:
                preview_text = _clean_tool_use_error(row.tool_result)
            elif row.tool_result_preview:
                preview_text = row.tool_result_preview
            elif row.tool_result:
                preview_text = row.tool_result
        if preview_text:
            title.append(f"  {_inline_preview(preview_text, 80)}", style="dim")
        return title

    if row.kind == "user_tool_result":
        has_error = "<tool_use_error>" in row.tool_result
        is_running = not row.tool_result
        is_expanded = bool(row.tool_result and not row.is_result_collapsed)
        title = Text()
        title.append("↳ ", style=TOOL_COLORS.get(row.tool_name, "bold #ffaa00"))
        title.append(row.tool_name, style=TOOL_COLORS.get(row.tool_name, "bold #ffaa00"))
        state_label = (
            "  error"
            if has_error
            else "  running"
            if is_running
            else "  expanded"
            if is_expanded
            else "  collapsed"
        )
        title.append(state_label, style="dim")
        if row.tool_args:
            args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in row.tool_args.items())
            title.append(f"  {args}", style="dim")

        body = Text()
        if has_error:
            error_text = _clean_tool_use_error(row.tool_result)
            body.append(error_text or "tool error", style="bold red")
        elif is_running:
            body.append("waiting for result", style="dim")
        elif row.tool_result and not row.is_result_collapsed:
            body.append("\n".join(row.tool_result.split("\n")[:20]), style=TEXT_DIM_STYLE)
        elif row.tool_result_preview:
            body.append(_inline_preview(row.tool_result_preview, 120), style="dim")
        elif row.tool_result:
            body.append("result available", style="dim")
        return Group(title, body)

    if row.kind == "assistant_text":
        if not row.text.strip():
            return Text("")
        text = Text()
        _append_wrapped_lines(text, row.text, first_prefix="", rest_prefix="", style=TEXT_DIM_STYLE)
        return text

    return Text(row.text)


def row_plain_text(row: TuiRow) -> str:
    if row.kind == "assistant_text":
        return row.text
    if row.kind == "assistant_thinking":
        return f"[thinking] {row.text[:500]}".strip()
    if row.kind == "assistant_tool_use":
        args = ", ".join(f"{k}={v!r}" for k, v in row.tool_args.items())
        return f"[tool] {row.tool_name}({args})".strip()
    if row.kind == "user_tool_result":
        return _clean_tool_use_error(row.tool_result)
    if row.kind == "user_input":
        return row.text
    return row.text


def to_plain_turn(
    turn,
    *,
    hide_past_thinking: bool = False,
    last_visible_thinking_row_id: str | None = None,
) -> PlainTurn:
    visible_rows = build_tui_rows_for_turn(
        turn,
        hide_past_thinking=hide_past_thinking,
        last_visible_thinking_row_id=last_visible_thinking_row_id,
    )
    visible_thinking_row_ids = {
        row.row_id for row in visible_rows if row.kind == "assistant_thinking"
    }
    plain_entries: list[PlainTurnEntry] = []
    for index, entry in enumerate(turn.entries):
        if entry.kind == "reasoning":
            row_id = f"turn-{turn.turn_id}-entry-{index}-thinking"
            if row_id not in visible_thinking_row_ids:
                continue
        plain_entries.append(
            PlainTurnEntry(
                kind=entry.kind,
                text=entry.text,
                tool_name=entry.tool_name,
                tool_args=entry.tool_args,
                tool_result=entry.tool_result,
                tool_result_preview=getattr(entry, "tool_result_preview", ""),
                is_result_collapsed=getattr(entry, "is_result_collapsed", True),
            )
        )
    return PlainTurn(
        turn_id=turn.turn_id,
        user_input=turn.user_input,
        entries=plain_entries,
    )


class TuiMessageRowWidget(Static):
    """A single stable message row."""

    DEFAULT_CSS = """
    TuiMessageRowWidget {
        height: auto;
        width: 100%;
    }
    """

    def __init__(self, row: TuiRow, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._row = row

    @property
    def row_data(self) -> TuiRow:
        return self._row

    def set_row(self, row: TuiRow) -> None:
        if row == self._row:
            return
        self._row = row
        self.update(render_tui_row(row))

    def on_mount(self) -> None:
        self.update(render_tui_row(self._row))

    def on_click(self) -> None:
        parent = self.parent
        while parent is not None and not isinstance(parent, TuiTurnWidget):
            parent = parent.parent

        if (
            self._row.kind in {"assistant_tool_use", "user_tool_result"}
            and self._row.tool_result
        ):
            if isinstance(parent, TuiTurnWidget):
                parent.toggle_tool_result(self._row.tool_call_id)
            return

        text = row_plain_text(self._row).strip()
        if text and copy_to_clipboard(text):
            self.styles.animate(
                "background",
                "#1a3040",
                duration=0.15,
                on_complete=lambda: self.styles.animate(
                    "background", "#0d1117", duration=0.4
                ),
            )


class TuiTurnWidget(Vertical):
    """A stable turn container with row-level children."""

    DEFAULT_CSS = """
    TuiTurnWidget {
        height: auto;
        width: 100%;
        margin: 0 0 1 0;
    }

    TuiTurnWidget > #turn-header {
        height: auto;
        width: 100%;
    }

    TuiTurnWidget > #turn-rows {
        height: auto;
        width: 100%;
    }
    """

    def __init__(self, turn, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._turn_data = turn
        self._header_text: Text | None = None
        self._hide_past_thinking = False
        self._last_visible_thinking_row_id: str | None = None

    async def on_mount(self) -> None:
        await self.sync_turn(self._turn_data)

    @property
    def turn_data(self):
        return self._turn_data

    def on_click(self) -> None:
        text = render_turn_as_plain_text(
            to_plain_turn(
                self._turn_data,
                hide_past_thinking=self._hide_past_thinking,
                last_visible_thinking_row_id=self._last_visible_thinking_row_id,
            )
        )
        if text and copy_to_clipboard(text):
            self.styles.animate(
                "background",
                "#1a3040",
                duration=0.15,
                on_complete=lambda: self.styles.animate(
                    "background", "#0d1117", duration=0.4
                ),
            )

    def toggle_tool_result(self, tool_call_id: str) -> None:
        for entry in self._turn_data.entries:
            if entry.kind == "tool_pair" and entry.tool_call_id == tool_call_id:
                set_turn_tool_result_collapsed(
                    self._turn_data,
                    tool_call_id,
                    not entry.is_result_collapsed,
                )
                self.run_worker(
                    self.sync_turn(
                        self._turn_data,
                        hide_past_thinking=self._hide_past_thinking,
                        last_visible_thinking_row_id=self._last_visible_thinking_row_id,
                    ),
                    group="turn-toggle-refresh",
                    exclusive=True,
                )
                return

    async def sync_turn(
        self,
        turn,
        *,
        hide_past_thinking: bool = False,
        last_visible_thinking_row_id: str | None = None,
    ) -> None:
        self._turn_data = turn
        self._hide_past_thinking = hide_past_thinking
        self._last_visible_thinking_row_id = last_visible_thinking_row_id
        header = self.query_one("#turn-header", Static)
        title = "System" if turn.turn_id == 0 else f"Turn {turn.turn_id}"
        header_text = Text()
        header_text.append(f"{title}", style="bold")
        if turn.status == "streaming":
            header_text.append("  streaming", style="dim")
        header_text.append("\n")
        header_text.append("─" * 56 + "\n", style="dim")
        if header_text != self._header_text:
            self._header_text = header_text
            header.update(header_text)

        rows_container = self.query_one("#turn-rows", Vertical)
        desired_rows = build_tui_rows_for_turn(
            turn,
            hide_past_thinking=hide_past_thinking,
            last_visible_thinking_row_id=last_visible_thinking_row_id,
        )
        desired_ids = [row.row_id for row in desired_rows]
        existing_widgets = {
            widget.id: widget for widget in rows_container.query(TuiMessageRowWidget)
        }

        async with rows_container.batch():
            for widget_id, widget in list(existing_widgets.items()):
                if widget_id not in desired_ids:
                    await widget.remove()
            for row in desired_rows:
                widget = existing_widgets.get(row.row_id)
                if widget is None:
                    widget = TuiMessageRowWidget(row, id=row.row_id)
                    await rows_container.mount(widget)
                else:
                    widget.set_row(row)

    def compose(self):
        yield Static("", id="turn-header")
        yield Vertical(id="turn-rows")
