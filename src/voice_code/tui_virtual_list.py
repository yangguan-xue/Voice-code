"""Virtualized message list for the Textual TUI."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Group, RenderableType
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from voice_code.transcript_view import render_turn_as_plain_text
from voice_code.tui_message_components import Messages
from voice_code.tui_models import TurnBlock, turn_to_plain

CopyCallback = Callable[[str, str], None]


class TurnWidget(Static):
    """A single clickable message row group."""

    def __init__(
        self,
        turn: TurnBlock,
        renderable: RenderableType,
        *,
        on_copy: CopyCallback | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(renderable, **kwargs)  # type: ignore[arg-type]
        self._turn_data = turn
        self._on_copy = on_copy

    @property
    def turn_data(self) -> TurnBlock:
        return self._turn_data

    def on_click(self) -> None:
        if self._on_copy is None:
            return
        text = render_turn_as_plain_text(turn_to_plain(self._turn_data))
        if text:
            self._on_copy(text, f"Copied turn ({len(text)} chars)")
            self._flash_selected()

    def _flash_selected(self) -> None:
        self.styles.animate(
            "background",
            "#1a3040",
            duration=0.15,
            on_complete=lambda: self.styles.animate(
                "background", "#0d1117", duration=0.4
            ),
        )


class MessagesView(VerticalScroll):
    """Messages -> Message -> subcomponent render path with light virtualization."""

    DEFAULT_CSS = """
    MessagesView {
        background: #0d1117;
        color: #e0e0e0;
        padding: 0 2;
    }
    #virtual-history {
        background: #0d1117;
        color: #e0e0e0;
        width: 100%;
    }
    """

    def __init__(
        self,
        *,
        on_copy: CopyCallback | None = None,
        estimated_turn_height: int = 14,
        buffer_turns: int = 5,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._turns: list[TurnBlock] = []
        self._on_copy = on_copy
        self._estimated_turn_height = estimated_turn_height
        self._buffer_turns = buffer_turns
        self._last_window: tuple[int, int] = (-1, -1)

    def compose(self):
        yield Vertical(id="virtual-history")

    def set_turns(self, turns: list[TurnBlock], *, force: bool = False) -> None:
        self._turns = list(turns)
        self.refresh_visible(force=force)

    def clear_turns(self) -> None:
        self._turns = []
        self._last_window = (-1, -1)
        self._clear_container()

    def refresh_visible(self, *, force: bool = False) -> None:
        if not self.is_mounted:
            return
        start, end = self._visible_window()
        if not force and (start, end) == self._last_window:
            return
        self._last_window = (start, end)

        container = self.query_one("#virtual-history", Vertical)
        for child in list(container.children):
            child.remove()

        if start > 0:
            top = Static("")
            top.styles.height = start * self._estimated_turn_height
            container.mount(top)

        for index, turn in enumerate(self._turns[start:end], start=start):
            if not turn.entries and not turn.user_input:
                continue
            container.mount(
                TurnWidget(
                    turn,
                    Group(*Messages(turn).render()),
                    on_copy=self._on_copy,
                    id=f"turn-{index}",
                )
            )

        if end < len(self._turns):
            bottom = Static("")
            bottom.styles.height = (len(self._turns) - end) * self._estimated_turn_height
            container.mount(bottom)

    def watch_scroll_y(self, *_: object) -> None:
        self.refresh_visible()

    def _visible_window(self) -> tuple[int, int]:
        if not self._turns:
            return 0, 0
        viewport_height = max(getattr(self.size, "height", 0) or 30, 1)
        first = max(0, int(self.scroll_y // self._estimated_turn_height) - self._buffer_turns)
        count = max(8, int(viewport_height // self._estimated_turn_height) + self._buffer_turns * 2)
        return first, min(len(self._turns), first + count)

    def _clear_container(self) -> None:
        if not self.is_mounted:
            return
        container = self.query_one("#virtual-history", Vertical)
        for child in list(container.children):
            child.remove()
