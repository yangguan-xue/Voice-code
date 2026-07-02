"""Textual widgets for the cc-haha-like TUI message tree."""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Static

from voice_code.clipboard import copy_to_clipboard
from voice_code.theme import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_PEACH,
    ACCENT_RED,
    TEXT_DIM,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TOOL_COLORS,
)
from voice_code.transcript_view import PlainTurn, PlainTurnEntry, render_turn_as_plain_text
from voice_code.tui_message_tree import (
    TuiRow,
    build_tui_rows_for_turn,
    set_turn_tool_result_collapsed,
)

TEXT_DIM_STYLE = f"dim {TEXT_DIM}"
PRIMARY_TOOL_ARG_KEYS = ("command", "pattern", "file_path", "path", "query", "url")
SECONDARY_TOOL_ARG_KEYS = ("cwd", "recursive", "limit", "offset")


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


def _format_tool_arg_value(value: Any, limit: int = 56) -> str:
    rendered = repr(value)
    if len(rendered) <= limit:
        return rendered
    head = max(16, limit // 2)
    tail = max(12, limit - head - 1)
    return f"{rendered[:head]}…{rendered[-tail:]}"


def _summarize_tool_args(tool_args: dict[str, Any], *, limit: int = 2) -> str:
    if not tool_args:
        return ""

    ordered_keys: list[str] = []
    for key in PRIMARY_TOOL_ARG_KEYS:
        if key in tool_args and key not in ordered_keys:
            ordered_keys.append(key)
    for key in SECONDARY_TOOL_ARG_KEYS:
        if key in tool_args and key not in ordered_keys:
            ordered_keys.append(key)
    for key in tool_args:
        if key not in ordered_keys:
            ordered_keys.append(key)

    selected: list[str] = []
    for key in ordered_keys:
        if key in {"path", "cwd"} and any(
            k in tool_args for k in ("command", "pattern", "file_path", "query", "url")
        ):
            continue
        selected.append(f"{key}={_format_tool_arg_value(tool_args[key])}")
        if len(selected) >= limit:
            break

    remaining = max(0, len(tool_args) - len(selected))
    summary = ", ".join(selected)
    if remaining:
        summary += f"  +{remaining} more"
    return summary


def _row_excerpt(text: str, limit: int = 56) -> str:
    preview = " ".join(text.strip().split())
    if len(preview) <= limit:
        return preview
    return preview[: limit - 1] + "…"


def render_tui_row(row: TuiRow) -> RenderableType:
    if row.kind == "system_info":
        text = Text()
        text.append("· ", style=f"dim {TEXT_DIM}")
        _append_wrapped_lines(
            text,
            row.text,
            first_prefix="",
            rest_prefix="  ",
            style=TEXT_DIM,
        )
        return text

    if row.kind == "system_error":
        text = Text()
        text.append("! ", style=f"bold {ACCENT_RED}")
        _append_wrapped_lines(
            text,
            row.text[:300],
            first_prefix="",
            rest_prefix="  ",
            style=f"bold {ACCENT_RED}",
        )
        return text

    if row.kind == "user_input":
        text = Text()
        text.append("user\n", style=f"bold {TEXT_SECONDARY}")
        _append_wrapped_lines(
            text,
            row.text,
            first_prefix="  ",
            rest_prefix="  ",
            style=TEXT_PRIMARY,
        )
        return text

    if row.kind == "assistant_thinking":
        if not row.text.strip():
            return Text("", style=f"dim {TEXT_DIM}")
        text = Text()
        text.append("··· ", style=f"dim {ACCENT_RED}")
        text.append(row.text, style=f"dim {TEXT_DIM}")
        return text

    if row.kind in ("assistant_tool_use", "user_tool_result"):
        return _render_tool_row(row)

    if row.kind == "assistant_text":
        if not row.text.strip():
            return Text("")
        from rich.markdown import Markdown
        return Group(
            Text("assistant\n", style=f"bold {ACCENT_RED}"),
            Markdown(row.text, code_theme="monokai"),
        )

    return Text(row.text)


def _render_tool_row(row: TuiRow) -> RenderableType:
    has_error = "<tool_use_error>" in row.tool_result
    is_running = (
        (row.kind == "assistant_tool_use" and not row.tool_result and row.is_streaming)
        or (row.kind == "user_tool_result" and not row.tool_result)
    )
    is_expanded = bool(row.tool_result and not row.is_result_collapsed)
    is_collapsed = bool(row.tool_result and row.is_result_collapsed)

    if has_error:
        state_chip = ("×", f"bold {ACCENT_RED}")
    elif is_running:
        state_chip = ("●", f"bold {ACCENT_BLUE}")
    elif is_expanded:
        state_chip = ("▾", f"bold {ACCENT_GREEN}")
    elif is_collapsed:
        state_chip = ("▸", f"bold {TEXT_DIM}")
    else:
        state_chip = ("•", f"dim {TEXT_DIM}")

    name_style = TOOL_COLORS.get(row.tool_name, f"bold {ACCENT_PEACH}")

    title = Text()
    title.append(f"{state_chip[0]} ", style=state_chip[1])
    title.append("tool ", style=f"dim {TEXT_DIM}")
    title.append(row.tool_name, style=name_style)

    if row.is_result_collapsed:
        preview = ""
        if has_error:
            preview = _clean_tool_use_error(row.tool_result)
        elif row.tool_result_preview:
            preview = row.tool_result_preview
        elif row.tool_result:
            preview = row.tool_result
        if preview:
            title.append("  ", style="dim")
            title.append(_inline_preview(preview, 80), style=TEXT_DIM)

    if row.kind == "assistant_tool_use" and row.tool_args:
        args = _summarize_tool_args(row.tool_args)
        title.append(f"  {args}", style=f"dim {TEXT_DIM}")

    body = Text()
    if has_error:
        error_text = _clean_tool_use_error(row.tool_result)
        body.append(error_text, style=ACCENT_RED)
    elif is_running:
        body.append("running…", style=f"italic {TEXT_DIM}")
    elif is_expanded:
        lines = row.tool_result.split("\n")[:20]
        for line in lines:
            body.append(f"  {line}\n", style=TEXT_SECONDARY)
    elif is_collapsed and not preview:
        body.append("result available", style=f"dim {TEXT_DIM}")
        body.append("\nclick to expand", style=f"dim {TEXT_DIM}")

    rendered = Group(title, body) if body.plain else Group(title, Text(""), body)
    return rendered


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
        margin: 0;
        padding: 0 2;
        background: #000000;
    }

    TuiMessageRowWidget.kind-system_info {
        background: #000000;
        color: #6f6961;
        padding: 0 2;
        margin: 0 0 1 0;
    }

    TuiMessageRowWidget.kind-system_error {
        background: #050000;
        color: #df6a5c;
        padding: 0 2;
        margin: 0 0 1 0;
    }

    TuiMessageRowWidget.kind-user_input {
        background: #000000;
        color: #ebe7de;
        padding: 1 2 0 2;
        margin: 1 0 1 0;
        border: none;
    }

    TuiMessageRowWidget.kind-assistant_text {
        background: #0b0b0b;
        color: #ebe7de;
        padding: 1 2 1 2;
        margin: 1 0;
        border: round #1c1c1c;
    }

    TuiMessageRowWidget.kind-assistant_thinking {
        background: #000000;
        color: #6f6961;
        padding: 0 2 1 2;
        margin: 0 0 1 0;
    }

    TuiMessageRowWidget.kind-assistant_tool_use {
        background: #050505;
        color: #a59f95;
        padding: 1 2 0 2;
        margin: 1 0 0 0;
        border-top: solid #1c1c1c;
    }

    TuiMessageRowWidget.kind-user_tool_result {
        background: #050505;
        color: #a59f95;
        padding: 0 2 1 2;
        margin: 0 0 1 0;
        border-bottom: solid #1c1c1c;
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
        padding: 0;
        background: #000000;
        border: none;
    }

    TuiTurnWidget > #turn-header {
        height: auto;
        width: 100%;
        margin: 0;
        color: #6f6961;
    }

    TuiTurnWidget > #turn-rows {
        height: auto;
        width: 100%;
    }

    TuiTurnWidget.turn-system {
        background: #000000;
        border: none;
    }

    TuiTurnWidget.turn-streaming {
        border: none;
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
        header_text.append(title, style=f"bold {TEXT_SECONDARY}")
        if turn.turn_id == 0:
            header_text.append(f"  {row_count} rows", style=f"dim {TEXT_DIM}")
        elif turn.status == "streaming":
            header_text.append("  live", style=f"dim {ACCENT_BLUE}")
        if header_text != self._header_text:
            self._header_text = header_text
            header.update(header_text)
            header.styles.display = "block" if turn.turn_id == 0 else "none"

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
