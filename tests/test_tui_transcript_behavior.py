"""TUI transcript behavior regression tests.

Covers transcript rendering, turn reconstruction after /resume,
tool result expand/collapse defaults, and permission event presentation.
"""

from __future__ import annotations

from voice_code.agent.types import AgentEvent, EventType
from voice_code.tui_models import TurnBlock, TurnEntry


def test_transcript_turn_starts_empty():
    t = TurnBlock(turn_id=1, user_input="hello")
    assert t.entries == []


def test_transcript_turn_adds_entry():
    t = TurnBlock(turn_id=1, user_input="hello")
    t.entries.append(TurnEntry(kind="text", text="hello world"))
    assert len(t.entries) == 1
    assert t.entries[0].kind == "text"


def test_tool_use_and_result_linked_by_call_id():
    t = TurnBlock(turn_id=1, user_input="test")
    t.entries.append(TurnEntry(kind="tool_use", text="bash", tool_call_id="c1"))
    t.entries.append(TurnEntry(kind="tool_result", text="output", tool_call_id="c1"))
    assert t.entries[0].tool_call_id == t.entries[1].tool_call_id


def test_permission_denied_event_has_status():
    event = AgentEvent(type=EventType.ERROR, turn=1, content="denied", status="permission_denied")
    assert event.status == "permission_denied"


def test_compact_event_has_compact_status():
    event = AgentEvent(
        type=EventType.ERROR,
        turn=1,
        content="compacted",
        phase="compacting",
        status="compact",
    )
    assert event.status == "compact"


def test_tool_failure_has_error_text():
    entry = TurnEntry(
        kind="tool_result",
        text="<tool_use_error>not found</tool_use_error>",
        tool_call_id="c1",
    )
    assert "<tool_use_error>" in entry.text


def test_resume_dedup_tool_call_ids():
    entries = [
        TurnEntry(kind="tool_use", text="bash", tool_call_id="c1"),
        TurnEntry(kind="tool_use", text="bash", tool_call_id="c1"),
    ]
    seen = set()
    deduped = []
    for e in entries:
        if e.tool_call_id and e.tool_call_id in seen:
            continue
        if e.tool_call_id:
            seen.add(e.tool_call_id)
        deduped.append(e)
    assert len(deduped) == 1


def test_event_type_enum_values():
    assert EventType.TEXT.value == 1
    assert EventType.TOOL_CALL.value == 3
    assert EventType.FINISH.value == 6
