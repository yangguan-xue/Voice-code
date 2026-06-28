"""Transcript/session tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from voice_code.session.manager import get_session_path, list_sessions, make_session_id
from voice_code.session.transcript import TranscriptReader, TranscriptWriter


def test_transcript_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)
    session_id = make_session_id()
    path = get_session_path(session_id)
    writer = TranscriptWriter(path)

    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="hello"),
        AIMessage(
            content="hi",
            tool_calls=[{"name": "read", "args": {"file_path": "x"}, "id": "tc_1"}],
        ),
        ToolMessage(content="result", tool_call_id="tc_1", name="read"),
    ]
    for msg in messages:
        writer.write_message(msg)
    writer.close()

    loaded = TranscriptReader(path).read_all()
    assert [m.type for m in loaded] == [m.type for m in messages]
    assert loaded[1].content == "hello"
    assert getattr(loaded[2], "tool_calls", [])[0]["name"] == "read"
    assert isinstance(loaded[3], ToolMessage)
    assert loaded[3].tool_call_id == "tc_1"


def test_transcript_read_info_extracts_title(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    writer = TranscriptWriter(path)
    writer.write_message(SystemMessage(content="sys"))
    writer.write_message(HumanMessage(content="first title"))
    writer.write_message(AIMessage(content="ok"))
    writer.close()

    info = TranscriptReader(path).read_info()
    assert info["title"] == "first title"
    assert info["message_count"] == 3


def test_transcript_writer_can_read_all_messages(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    writer = TranscriptWriter(path)
    writer.write_message(SystemMessage(content="sys"))
    writer.write_message(HumanMessage(content="hello"))
    writer.write_message(AIMessage(content="world"))

    loaded = writer.read_all_messages()

    assert [msg.type for msg in loaded] == ["system", "human", "ai"]
    assert loaded[-1].content == "world"
    writer.close()


def test_list_sessions_returns_recent_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    path1 = get_session_path("20260101-000000-abcd")
    writer1 = TranscriptWriter(path1)
    writer1.write_message(HumanMessage(content="session one"))
    writer1.close()

    path2 = get_session_path("20260101-000001-efgh")
    writer2 = TranscriptWriter(path2)
    writer2.write_message(HumanMessage(content="session two"))
    writer2.close()

    sessions = list_sessions(limit=10)
    assert len(sessions) == 2
    assert sessions[0]["title"] in {"session one", "session two"}
    assert "id" in sessions[0]
