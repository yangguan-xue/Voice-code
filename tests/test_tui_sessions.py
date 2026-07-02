"""Session sidebar rendering tests."""

from __future__ import annotations

import pytest
from textual.app import App

from voice_code.session.manager import SessionGroup, SessionSummary
from voice_code.tui_sessions import (
    SessionSelected,
    SessionSidebarItem,
    SessionSidebarView,
    _relative_time_label,
    _sidebar_title,
)


class _SidebarProbeApp(App[None]):
    CSS = "Screen { background: #000000; }"

    def __init__(self) -> None:
        super().__init__()
        self.selected: list[str] = []

    def compose(self):
        yield SessionSidebarView(id="sidebar")

    def on_mount(self) -> None:
        self.query_one("#sidebar", SessionSidebarView).run_worker(
            self.query_one("#sidebar", SessionSidebarView).sync_groups(
                [
                    SessionGroup(
                        key="/Users/example/programs/reasoning",
                        label="reasoning",
                        project_path="/Users/example/programs/reasoning",
                        sessions=[
                            SessionSummary(
                                id="session-1",
                                title="优化 agent UI 观感",
                                message_count=12,
                                updated_at="2026-07-01 10:00:00",
                                project_label="reasoning",
                                project_path="/Users/example/programs/reasoning",
                                is_current=True,
                            ),
                            SessionSummary(
                                id="session-2",
                                title="梳理项目顶层模块",
                                message_count=6,
                                updated_at="2026-07-01 11:00:00",
                                project_label="reasoning",
                                project_path="/Users/example/programs/reasoning",
                                is_current=False,
                            ),
                        ],
                    )
                ]
            ),
            group="probe-session-sidebar",
            exclusive=True,
        )

    def on_session_selected(self, event: SessionSelected) -> None:
        self.selected.append(event.session_id)


def test_relative_time_label_uses_recent_units():
    label = _relative_time_label("2099-01-01 00:00:00")

    assert label == "刚刚"


def test_sidebar_title_falls_back_and_truncates_for_navigation_density():
    long_title = "前端代码在 frontend/ 目录下，但我不希望你碰它，我自己来改。"

    assert _sidebar_title("") == "新对话"
    assert _sidebar_title(long_title).endswith("…")
    assert len(_sidebar_title(long_title)) <= 16


@pytest.mark.asyncio
async def test_session_sidebar_view_renders_clickable_item():
    app = _SidebarProbeApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        item = app.query_one("#session-session-1", SessionSidebarItem)

        assert item.session.title == "优化 agent UI 观感"
        assert item.has_class("current-session")


@pytest.mark.asyncio
async def test_session_sidebar_view_click_emits_selection():
    app = _SidebarProbeApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#session-session-1")

        assert app.selected == ["session-1"]


@pytest.mark.asyncio
async def test_session_sidebar_view_keyboard_navigation_moves_active_item():
    app = _SidebarProbeApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar", SessionSidebarView)
        item1 = app.query_one("#session-session-1", SessionSidebarItem)
        item2 = app.query_one("#session-session-2", SessionSidebarItem)

        sidebar.focus_active_session()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()

        assert item1.has_class("active-session") is False
        assert item2.has_class("active-session")


@pytest.mark.asyncio
async def test_session_sidebar_view_keyboard_enter_resumes_active_item():
    app = _SidebarProbeApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar", SessionSidebarView)

        sidebar.focus_active_session()
        await pilot.pause()
        await pilot.press("down", "enter")

        assert app.selected == ["session-2"]
