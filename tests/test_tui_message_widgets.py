"""TUI message widget regression tests."""

from __future__ import annotations

import pytest
from textual.app import App

from voice_code.tui import TurnBlock, TurnEntry
from voice_code.tui_message_widgets import (
    TuiMessageRowWidget,
    TuiTurnWidget,
    _summarize_tool_args,
)


class _TurnProbeApp(App[None]):
    def compose(self):
        yield TuiTurnWidget(
            TurnBlock(
                turn_id=1,
                user_input="hello",
                entries=[TurnEntry(kind="text", text="world")],
            ),
            id="turn",
        )


@pytest.mark.asyncio
async def test_turn_widget_renders_rows_on_initial_mount():
    app = _TurnProbeApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one("#turn", TuiTurnWidget)
        rows = list(widget.query(TuiMessageRowWidget))

        assert len(rows) == 2
        assert widget._header_text is not None
        assert "Turn 01" in str(widget._header_text)


def test_summarize_tool_args_prioritizes_pattern_over_path():
    summary = _summarize_tool_args(
        {
            "pattern": "**/*.md",
            "path": "/Users/example/work/workspace/workspace/voice-code",
        }
    )

    assert "pattern=" in summary
    assert "path=" not in summary


def test_summarize_tool_args_shows_command_and_collapses_extra_fields():
    summary = _summarize_tool_args(
        {
            "command": "rg --files src",
            "cwd": "/Users/example/work/workspace/workspace/voice-code",
            "limit": 50,
        }
    )

    assert "command=" in summary
    assert "+1 more" in summary or "+2 more" in summary
