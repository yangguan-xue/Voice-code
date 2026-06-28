"""TUI message widget regression tests."""

from __future__ import annotations

import pytest
from textual.app import App

from reasoning_agent.tui import TurnBlock, TurnEntry
from reasoning_agent.tui_message_widgets import TuiMessageRowWidget, TuiTurnWidget


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
        assert "Turn 1" in str(widget._header_text)
