"""Textual widgets for the cc-haha-like TUI message tree."""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Static

from voice_code.theme import TOOL_COLORS, TEXT_DIM
from voice_code.clipboard import copy_to_clipboard
from voice_code.transcript_view import PlainTurn, PlainTurnEntry, render_turn_as_plain_text
from voice_code.tui_message_tree import (
    TuiRow,
    build_tui_rows_for_turn,
    set_turn_tool_result_collapsed,
)

TEXT_DIM_STYLE = f"dim {TEXT_DIM}"


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


def _row_excerpt(text: str, limit: int = 56) -> str:
    preview = " ".join(text.strip().split())
    if len(preview) <= limit:
        return preview
    return preview[: limit - 1] + "…"


def render_tui_row(row: TuiRow) -> RenderableType:
    if row.kind == "system_info":
        text = Text()
        text.append("status\n", style="bold #666666")
        _append_wrapped_lines(
            text,
            row.text,
            first_prefix="• ",
            rest_prefix="  ",
            style="#999999",
        )
        return text

    if row.kind == "system_error":
        text = Text()
        text.append("error\n", style="bold #ff6666")
        _append_wrapped_lines(
            text,
            row.text[:300],
            first_prefix="• ",
            rest_prefix="  ",
            style="bold #ff6666",
        )
        return text

    if row.kind == "user_input":
        text = Text()
        text.append("you\n", style="bold #33cc33")
        _append_wrapped_lines(
            text,
            row.text,
            first_prefix="> ",
            rest_prefix="  ",
            style="bold #e0e0e0",
        )
        return text

    if row.kind == "assistant_thinking":
        if not row.text.strip():
            return Text("thinking\n模型正在整理上下文…", style="italic #999999")
        return Group(
            Text("thinking", style="italic #666666"),
            Text(row.text, style="#999999"),
        )

    if row.kind == "assistant_tool_use":
        args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in row.tool_args.items())
        title = Text()
        title.append("tool ", style="bold #666666")
        title.append("● ", style=TOOL_COLORS.get(row.tool_name, "bold #ffaa00"))
        title.append(row.tool_name, style=TOOL_COLORS.get(row.tool_name, "bold #ffaa00"))
        if args:
            title.append(f"  {args}", style="#999999")
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
        title.append(state_label, style="dim #666666")
        preview_text = ""
        if row.is_result_collapsed:
            if has_error:
                preview_text = _clean_tool_use_error(row.tool_result)
            elif row.tool_result_preview:
                preview_text = row.tool_result_preview
            elif row.tool_result:
                preview_text = row.tool_result
        body = Text()
        if preview_text:
            body.append(_inline_preview(preview_text, 110), style="#999999")
            if has_result:
                body.append("\nclick row to expand or collapse", style="dim #555555")
        elif is_running:
            body.append("waiting for result", style="italic #666666")
        return Group(title, body) if body.plain else title

    if row.kind == "user_tool_result":
        has_error = "<tool_use_error>" in row.tool_result
        is_running = not row.tool_result
        is_expanded = bool(row.tool_result and not row.is_result_collapsed)
        title = Text()
        title.append("result ", style="bold #666666")
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
            title.append(f"  {args}", style="#666666")

        body = Text()
        if has_error:
            error_text = _clean_tool_use_error(row.tool_result)
            body.append(error_text or "tool error", style="bold #ff6666")
        elif is_running:
            body.append("waiting for result", style="italic #666666")
        elif row.tool_result and not row.is_result_collapsed:
            lines = row.tool_result.split("\n")[:20]
            formatted = "\n".join(f"  {line}" if line else "" for line in lines)
            body.append(formatted, style="#cccccc")
        elif row.tool_result_preview:
            body.append(_inline_preview(row.tool_result_preview, 120), style="#999999")
            body.append("\nclick row to expand or collapse", style="dim #555555")
        elif row.tool_result:
            body.append("result available", style="#666666")
        return Group(title, body)

    if row.kind == "assistant_text":
        if not row.text.strip():
            return Text("")
        from rich.markdown import Markdown
        return Group(
            Text("assistant\n", style="bold #e0e0e0"),
            Markdown(row.text, code_theme="monokai"),
        )

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
        margin: 0 0 1 0;
        padding: 0 1;
        background: #000000;
        border-left: solid #222222;
    }

    TuiMessageRowWidget.kind-system_info {
        background: #000000;
        border-left: solid #333333;
        color: #999999;
    }

    TuiMessageRowWidget.kind-system_error {
        background: #1a0000;
        border-left: solid #cc2222;
        color: #ffaaaa;
    }

    TuiMessageRowWidget.kind-user_input {
        background: #000000;
        border-left: solid #228833;
        color: #cccccc;
        padding: 1;
    }

    TuiMessageRowWidget.kind-assistant_text {
        background: #0a0a0a;
        border-left: solid #333333;
        color: #cccccc;
        padding: 1;
    }

    TuiMessageRowWidget.kind-assistant_thinking {
        background: #000000;
        border-left: solid #333333;
        color: #999999;
        padding: 1;
    }

    TuiMessageRowWidget.kind-assistant_tool_use {
        background: #0a0a0a;
        border-left: solid #444444;
        color: #cccccc;
        padding: 1;
    }

    TuiMessageRowWidget.kind-user_tool_result {
        background: #000000;
        border-left: solid #333333;
        color: #cccccc;
        padding: 0 1 1 2;
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
        self._sync_kind_classes()
        self.update(render_tui_row(row))

    def on_mount(self) -> None:
        self._sync_kind_classes()
        self.update(render_tui_row(self._row))

    def _sync_kind_classes(self) -> None:
        for kind in (
            "system_info",
            "system_error",
            "user_input",
            "assistant_text",
            "assistant_thinking",
            "assistant_tool_use",
            "user_tool_result",
        ):
            self.set_class(self._row.kind == kind, f"kind-{kind}")

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
                "#222222",
                duration=0.15,
                on_complete=lambda: self.styles.animate(
                    "background", "#000000", duration=0.4
                ),
            )


class TuiTurnWidget(Vertical):
    """A stable turn container with row-level children."""

    DEFAULT_CSS = """
    TuiTurnWidget {
        height: auto;
        width: 100%;
        margin: 0 0 1 0;
        padding: 1;
        background: #000000;
        border: round #222222;
    }

    TuiTurnWidget > #turn-header {
        height: auto;
        width: 100%;
        margin: 0 0 1 0;
        color: #666666;
    }

    TuiTurnWidget > #turn-rows {
        height: auto;
        width: 100%;
    }

    TuiTurnWidget.turn-system {
        background: #000000;
        border: round #222222;
    }

    TuiTurnWidget.turn-streaming {
        border: round #333333;
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
                "#222222",
                duration=0.15,
                on_complete=lambda: self.styles.animate(
                    "background", "#000000", duration=0.4
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
        self.set_class(turn.turn_id == 0, "turn-system")
        self.set_class(turn.status == "streaming", "turn-streaming")
        title = "Session" if turn.turn_id == 0 else f"Turn {turn.turn_id:02d}"
        row_count = len(build_tui_rows_for_turn(
            turn,
            hide_past_thinking=hide_past_thinking,
            last_visible_thinking_row_id=last_visible_thinking_row_id,
        ))
        header_text = Text()
        header_text.append(f"{title}", style="bold #e0e0e0")
        if turn.status == "streaming":
            header_text.append("  live", style="bold #ffffff on #4488ff")
        elif turn.turn_id != 0:
            header_text.append("  complete", style="dim #666666")
        header_text.append(f"  {row_count} rows", style="dim #666666")
        if turn.user_input:
            header_text.append("  ")
            header_text.append(_row_excerpt(turn.user_input), style="#999999")
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
