"""Session sidebar widgets and rendering helpers for the TUI."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from rich.text import Text
from textual import events
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static

from voice_code.session import SessionGroup, SessionSummary
from voice_code.theme import (
    ACCENT_BRIGHT_RED,
    ACCENT_RED,
    BG_ELEVATED,
    BG_SECONDARY,
    BORDER_PRIMARY,
    BORDER_SECONDARY,
    TEXT_BRIGHT,
    TEXT_DIM,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


def _relative_time_label(updated_at: str) -> str:
    try:
        ts = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return updated_at

    delta = datetime.now() - ts
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60}分"
    if seconds < 86400:
        return f"{seconds // 3600}时"
    if seconds < 86400 * 7:
        return f"{seconds // 86400}天"
    return ts.strftime("%m-%d")


def _sidebar_title(title: str, *, max_chars: int = 16) -> str:
    normalized = " ".join(title.split()).strip()
    if not normalized:
        return "新对话"
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1] + "…"


class SessionSelected(Message):
    """Emitted when a history session is chosen from the sidebar."""

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id


class SessionSidebarItem(Static, can_focus=True):
    """A single clickable session summary row."""

    DEFAULT_CSS = f"""
    SessionSidebarItem {{
        width: 100%;
        height: auto;
        margin: 0 0 0 0;
        padding: 0 0 0 2;
        background: transparent;
        border: none;
        border-left: tall transparent;
        color: {TEXT_PRIMARY};
    }}

    SessionSidebarItem:hover {{
        background: {BG_SECONDARY};
        border-left: tall {BORDER_PRIMARY};
    }}

    SessionSidebarItem:focus {{
        background: {BG_SECONDARY};
        border-left: tall {ACCENT_BRIGHT_RED};
    }}

    SessionSidebarItem.active-session {{
        background: {BG_SECONDARY};
        border-left: tall {BORDER_SECONDARY};
    }}

    SessionSidebarItem.current-session {{
        background: {BG_ELEVATED};
        border-left: tall {ACCENT_RED};
    }}

    SessionSidebarItem.current-session.active-session {{
        background: {BG_ELEVATED};
        border-left: tall {ACCENT_BRIGHT_RED};
    }}
    """

    def __init__(self, session: SessionSummary, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self.session = session
        self._is_active = False

    def on_mount(self) -> None:
        self._sync_state()

    def update_session(self, session: SessionSummary) -> None:
        self.session = session
        self._sync_state()

    def set_active(self, is_active: bool) -> None:
        self._is_active = is_active
        self._sync_state()

    def on_click(self) -> None:
        self.focus()
        self.post_message(SessionSelected(self.session.id))

    def on_focus(self) -> None:
        parent = self._sidebar_parent()
        if parent is not None:
            parent.set_active_session(self.session.id)

    def on_key(self, event: events.Key) -> None:
        if event.key in {"enter", "space"}:
            event.stop()
            self.post_message(SessionSelected(self.session.id))
            return
        if event.key in {"up", "k"}:
            event.stop()
            parent = self._sidebar_parent()
            if parent is not None:
                parent.move_active(-1)
            return
        if event.key in {"down", "j"}:
            event.stop()
            parent = self._sidebar_parent()
            if parent is not None:
                parent.move_active(1)

    def _sidebar_parent(self) -> SessionSidebarView | None:
        parent = self.parent
        while parent is not None and not isinstance(parent, SessionSidebarView):
            parent = parent.parent
        return parent

    def _sync_state(self) -> None:
        self.set_class(self.session.is_current, "current-session")
        self.set_class(self._is_active, "active-session")
        self.update(self._render_summary())

    def _render_summary(self) -> Text:
        text = Text()
        bullet_style = ACCENT_RED if self.session.is_current else TEXT_DIM
        title_style = f"bold {TEXT_BRIGHT}" if self.session.is_current else TEXT_PRIMARY

        text.append("● " if self.session.is_current else "", style=bullet_style)
        text.append(_sidebar_title(self.session.title), style=title_style)
        text.append("\n", style=TEXT_DIM)
        text.append(_relative_time_label(self.session.updated_at), style=TEXT_DIM)
        text.append(" · ", style=TEXT_DIM)
        text.append(f"{self.session.message_count} 条", style=TEXT_SECONDARY)
        return text


class SessionSidebarView(VerticalScroll):
    """Scrollable project-grouped session history sidebar."""

    DEFAULT_CSS = f"""
    SessionSidebarView {{
        background: #000000;
        scrollbar-background: #000000;
        scrollbar-color: #232323;
        scrollbar-color-hover: #303030;
        scrollbar-corner-color: #000000;
    }}

    SessionSidebarView > #session-sidebar-list {{
        width: 100%;
        height: auto;
    }}

    SessionSidebarView .group-heading {{
        width: 100%;
        margin: 1 0 0 0;
        padding: 0 0 1 0;
        color: {TEXT_DIM};
        border-bottom: solid {BORDER_PRIMARY};
    }}

    SessionSidebarView .session-empty {{
        width: 100%;
        padding: 1 0 1 2;
        color: {TEXT_DIM};
        border-left: solid {BORDER_PRIMARY};
    }}
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._groups: list[SessionGroup] = []
        self._active_session_id: str | None = None

    def compose(self):
        yield Vertical(id="session-sidebar-list")

    async def sync_groups(self, groups: Iterable[SessionGroup]) -> None:
        self._groups = list(groups)
        container = self.query_one("#session-sidebar-list", Vertical)
        await container.remove_children()

        if not self._groups:
            empty = Text()
            empty.append("暂无历史会话\n", style=f"bold {TEXT_PRIMARY}")
            empty.append("新对话开始后，这里会按项目自动归档。", style=TEXT_DIM)
            await container.mount(Static(empty, classes="session-empty"))
            self._active_session_id = None
            return

        next_active_session_id = self._resolve_active_session_id()
        for group in self._groups:
            heading = Text()
            heading.append(group.label, style=f"bold {TEXT_PRIMARY}")
            heading.append("  ", style=TEXT_DIM)
            heading.append(str(len(group.sessions)), style=TEXT_DIM)
            await container.mount(Static(heading, classes="group-heading"))
            if not group.sessions:
                await container.mount(Static("暂无会话", classes="session-empty"))
                continue
            for session in group.sessions:
                await container.mount(
                    SessionSidebarItem(
                        session,
                        id=f"session-{session.id}",
                    )
                )
        self.set_active_session(next_active_session_id)

    def focus_active_session(self) -> None:
        item = self._active_item()
        if item is not None:
            item.focus()

    def set_active_session(self, session_id: str | None) -> None:
        self._active_session_id = session_id
        for item in self.query(SessionSidebarItem):
            item.set_active(item.session.id == session_id)

    def move_active(self, delta: int) -> None:
        items = list(self.query(SessionSidebarItem))
        if not items:
            return
        current_index = 0
        if self._active_session_id is not None:
            for index, item in enumerate(items):
                if item.session.id == self._active_session_id:
                    current_index = index
                    break
        next_index = max(0, min(len(items) - 1, current_index + delta))
        target = items[next_index]
        self.set_active_session(target.session.id)
        target.focus()
        target.scroll_visible(animate=False)

    def _active_item(self) -> SessionSidebarItem | None:
        for item in self.query(SessionSidebarItem):
            if item.session.id == self._active_session_id:
                return item
        return None

    def _resolve_active_session_id(self) -> str | None:
        if self._active_session_id:
            for group in self._groups:
                for session in group.sessions:
                    if session.id == self._active_session_id:
                        return self._active_session_id
        for group in self._groups:
            for session in group.sessions:
                if session.is_current:
                    return session.id
        for group in self._groups:
            if group.sessions:
                return group.sessions[0].id
        return None
