"""TUI message component rendering tests."""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from voice_code.tui_message_components import Message, Messages
from voice_code.tui_models import TurnBlock, TurnEntry


def test_message_routes_entries_to_specialized_renderers():
    text = Message(TurnEntry(kind="text", text="hello")).render()
    info = Message(TurnEntry(kind="info", text="ready")).render()
    tool = Message(
        TurnEntry(
            kind="tool_pair",
            tool_name="read",
            tool_args={"file_path": "a.py"},
            tool_result="ok",
        )
    ).render()

    assert isinstance(text, Panel)
    assert isinstance(info, Text)
    assert isinstance(tool, Panel)


def test_messages_renders_turn_header_user_and_entries():
    turn = TurnBlock(
        turn_id=1,
        user_input="inspect files",
        entries=[TurnEntry(kind="text", text="done")],
    )

    renderables = Messages(turn).render()

    assert len(renderables) == 3
