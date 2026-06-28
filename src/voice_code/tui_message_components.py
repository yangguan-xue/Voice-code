"""Message renderers for the Textual TUI.

This mirrors cc-haha's split between Message.tsx and components/messages/*:
Message routes a structured entry to a specialized renderer, while the screen
only owns state and dispatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from voice_code.theme import (
    BG_SURFACE,
    BG_SECONDARY,
    BG_CODE,
    BORDER_PRIMARY,
    BORDER_SECONDARY,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_DIM,
    TOOL_COLORS,
    CODE_THEME,
)
from voice_code.tui_models import TurnBlock, TurnEntry

CODE_INFO_STYLE = f"dim {TEXT_DIM}"
TEXT_DIM_STYLE = "dim"
CODE_LANG_STYLE = f"bold {TEXT_SECONDARY}"
CODE_BACKGROUND = BG_CODE
ASSISTANT_PANEL_BORDER = BORDER_SECONDARY
ASSISTANT_PANEL_BG = BG_SECONDARY
ASSISTANT_TITLE_STYLE = f"bold {TEXT_PRIMARY}"
TOOL_PANEL_BG = BG_SURFACE
TOOL_PANEL_BORDER = BORDER_PRIMARY
THINKING_PANEL_BORDER = BORDER_SECONDARY
THINKING_PANEL_BG = BG_SURFACE


@dataclass
class ContentBlock:
    """A plain text or fenced-code segment."""

    kind: str
    text: str
    info: str = ""


def split_content_blocks(text: str) -> list[ContentBlock]:
    if not text:
        return []

    blocks: list[ContentBlock] = []
    fence_re = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
    position = 0

    for match in fence_re.finditer(text):
        if match.start() > position:
            plain = text[position:match.start()].strip("\n")
            if plain:
                blocks.append(ContentBlock(kind="text", text=plain))

        blocks.append(
            ContentBlock(
                kind="code",
                text=match.group(2).rstrip("\n"),
                info=match.group(1).strip(),
            )
        )
        position = match.end()

    if position < len(text):
        plain = text[position:].strip("\n")
        if plain:
            blocks.append(ContentBlock(kind="text", text=plain))

    return blocks


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


class AssistantTextMessage:
    def __init__(self, text: str) -> None:
        self.text = text

    def render(self) -> Panel:
        return Panel(
            Markdown(
                self.text,
                code_theme=CODE_THEME,
                inline_code_theme=CODE_THEME,
            ),
            border_style=ASSISTANT_PANEL_BORDER,
            style=f"on {ASSISTANT_PANEL_BG}",
            padding=(0, 0),
            title=Text(" assistant ", style=ASSISTANT_TITLE_STYLE),
            title_align="left",
            expand=True,
        )


class AssistantThinkingMessage:
    def __init__(self, text: str) -> None:
        self.text = text

    def render(self) -> Panel:
        return Panel(
            Text(self.text, style="dim"),
            title=Text(" thinking ", style="bold #666666"),
            border_style=THINKING_PANEL_BORDER,
            style=f"on {THINKING_PANEL_BG}",
            padding=(0, 1),
            expand=True,
        )


class InfoMessage:
    def __init__(self, text: str) -> None:
        self.text = text

    def render(self) -> Text:
        rendered = Text()
        _append_wrapped_lines(
            rendered,
            self.text,
            first_prefix="i ",
            rest_prefix="  ",
            style=TEXT_DIM_STYLE,
        )
        return rendered


class ErrorMessage:
    def __init__(self, text: str) -> None:
        self.text = text

    def render(self) -> Text:
        rendered = Text()
        _append_wrapped_lines(
            rendered,
            self.text[:300],
            first_prefix="x ",
            rest_prefix="  ",
            style="bold red",
        )
        return rendered


class ToolUseMessage:
    def __init__(self, entry: TurnEntry) -> None:
        self.entry = entry

    def render(self) -> Panel:
        entry = self.entry
        args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in entry.tool_args.items())
        if entry.tool_result and entry.is_result_collapsed:
            hint = " [> /detail]"
        elif entry.tool_result:
            hint = " [v /collapse]"
        else:
            hint = " [... running]"

        body: list[RenderableType] = []
        title = Text()
        has_error = "<tool_use_error>" in entry.tool_result
        is_running = not entry.tool_result
        is_expanded = bool(entry.tool_result and not entry.is_result_collapsed)
        if has_error:
            state_text = "error"
            state_style = "bold red"
            border_style = "red"
        elif is_running:
            state_text = "running"
            state_style = "bold #ffaa00"
            border_style = "#ffaa00"
        elif is_expanded:
            state_text = "expanded"
            state_style = "bold #33cc33"
            border_style = "#33cc33"
        else:
            state_text = "collapsed"
            state_style = "bold #666666"
            border_style = TOOL_PANEL_BORDER

        chevron = "v" if is_expanded else ">" if entry.tool_result else "*"
        title.append(f"{chevron} ", style=state_style)
        title.append("* ", style=TOOL_COLORS.get(entry.tool_name, "bold #ffaa00"))
        title.append(entry.tool_name, style=TOOL_COLORS.get(entry.tool_name, "bold #ffaa00"))
        title.append(f"  {state_text}", style=state_style)
        if args:
            title.append(f"  {args}", style="dim")

        summary = Text(style="dim")
        if has_error:
            summary.append("tool error", style="bold red")
        elif is_running:
            summary.append("waiting for result")
        elif entry.tool_result_preview:
            preview = entry.tool_result_preview.strip().replace("\n", " ")
            summary.append(preview[:120] + ("..." if len(preview) > 120 else ""))
        elif entry.tool_result:
            summary.append("result available")

        if summary.plain:
            body.append(summary)
            if entry.tool_result and not entry.is_result_collapsed:
                body.append(Text(""))

        if entry.tool_result and not entry.is_result_collapsed:
            detail_lines = "\n".join(entry.tool_result.split("\n")[:20])
            if entry.tool_result.lstrip().startswith(("#", "-", "*", "```")):
                body.append(Markdown(detail_lines, code_theme=CODE_THEME))
            else:
                body.extend(render_content_blocks(detail_lines, text_style=TEXT_DIM_STYLE))
        elif not summary.plain:
            body.append(Text("running...", style="dim"))

        return Panel(
            Group(*body),
            title=title,
            subtitle=hint.strip(),
            subtitle_align="right",
            border_style=border_style,
            style=f"on {TOOL_PANEL_BG}",
            padding=(0, 1),
            expand=True,
        )


def _make_code_block(block: ContentBlock) -> Group:
    header = Text("code", style=CODE_INFO_STYLE)
    if block.info:
        header.append(f" {block.info}", style=CODE_LANG_STYLE)
    syntax = Syntax(
        block.text or "",
        lexer=block.info or "text",
        theme=CODE_THEME,
        line_numbers=True,
        word_wrap=False,
        background_color=CODE_BACKGROUND,
        indent_guides=False,
        padding=(0, 1),
    )
    return Group(header, syntax)


def render_content_blocks(
    text: str,
    *,
    first_prefix: str = "",
    rest_prefix: str = "",
    text_style: str = "white",
) -> list[RenderableType]:
    blocks = split_content_blocks(text)
    if not blocks:
        single = Text()
        _append_wrapped_lines(
            single,
            text,
            first_prefix=first_prefix,
            rest_prefix=rest_prefix,
            style=text_style,
        )
        return [single]

    rendered_blocks: list[RenderableType] = []
    for block_index, block in enumerate(blocks):
        if block.kind == "text":
            piece = Text()
            _append_wrapped_lines(
                piece,
                block.text,
                first_prefix=first_prefix,
                rest_prefix=rest_prefix,
                style=text_style,
            )
            rendered_blocks.append(piece)
        else:
            rendered_blocks.append(_make_code_block(block))
        if block_index != len(blocks) - 1:
            rendered_blocks.append(Text("\n"))
    return rendered_blocks


class Message:
    """Route a TurnEntry to its specialized message component."""

    def __init__(self, entry: TurnEntry) -> None:
        self.entry = entry

    def render(self) -> RenderableType:
        if self.entry.kind == "info":
            return InfoMessage(self.entry.text).render()
        if self.entry.kind == "error":
            return ErrorMessage(self.entry.text).render()
        if self.entry.kind == "reasoning":
            return AssistantThinkingMessage(self.entry.text).render()
        if self.entry.kind == "text":
            return AssistantTextMessage(self.entry.text).render()
        return ToolUseMessage(self.entry).render()


class Messages:
    """Render one structured turn into message-row renderables."""

    def __init__(self, turn: TurnBlock) -> None:
        self.turn = turn

    def render(self) -> list[RenderableType]:
        renderables: list[RenderableType] = []
        title = "System" if self.turn.turn_id == 0 else f"Turn {self.turn.turn_id}"
        header = Text()
        header.append(title, style="bold")
        if self.turn.status == "streaming":
            header.append("  streaming", style="dim")
        header.append("\n")
        header.append("-" * 56 + "\n", style="dim")
        renderables.append(header)

        if self.turn.user_input:
            user_text = Text()
            _append_wrapped_lines(
                user_text,
                self.turn.user_input,
                first_prefix="> ",
                rest_prefix="  ",
                style="bold #33cc33",
            )
            renderables.append(user_text)

        for entry in self.turn.entries:
            renderables.append(Message(entry).render())
        return renderables
