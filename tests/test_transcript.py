"""Transcript/session tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from voice_code.session.lifecycle import archive_session
from voice_code.session.manager import (
    get_session_path,
    group_session_summaries,
    list_session_summaries,
    list_sessions,
    make_session_id,
)
from voice_code.session.state import build_session_runtime_state, save_session_state
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
    assert info["cwd"] == ""


def test_transcript_read_info_extracts_cwd_from_session_meta(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    writer = TranscriptWriter(path, session_meta={"cwd": "/Users/example/workspace/reasoning"})
    writer.write_message(HumanMessage(content="first title"))
    writer.close()

    info = TranscriptReader(path).read_info()

    assert info["cwd"] == "/Users/example/workspace/reasoning"
    assert info["title"] == "first title"


def test_transcript_read_info_summarizes_multiline_memory_blob_title(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    writer = TranscriptWriter(path)
    writer.write_message(
        HumanMessage(
            content=(
                "<memories>\n"
                "## User Memories\n\n"
                "# Memories\n\n"
                "- [前端代码在 frontend/ 目录下，但我不希望你碰它，我自己来改。]"
                "(entries/mem_x.md)\n"
            )
        )
    )
    writer.close()

    info = TranscriptReader(path).read_info()

    assert info["title"].startswith("前端代码在 frontend/ 目录下")
    assert info["title"].endswith("…")


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


def test_transcript_reader_skips_corrupt_jsonl_lines(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        '{"type":"human","content":"first"}\n{broken\n{"type":"ai","content":"ok"}\n',
        encoding="utf-8",
    )

    messages = TranscriptReader(path).read_all()
    info = TranscriptReader(path).read_info()

    assert [message.content for message in messages] == ["first", "ok"]
    assert info["message_count"] == 2
    assert info["corrupt_record_count"] == 1


def test_list_session_summaries_skips_unreadable_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)
    good_path = get_session_path("20260101-000000-abcd")
    writer = TranscriptWriter(good_path)
    writer.write_message(HumanMessage(content="good session"))
    writer.close()
    bad_path = get_session_path("20260101-000001-bad0")
    bad_path.write_text('{"type":"unknown","content":"bad"}\n', encoding="utf-8")

    summaries = list_session_summaries(limit=10)

    assert [summary.id for summary in summaries] == ["20260101-000000-abcd"]


def test_transcript_writer_rotates_when_size_limit_is_exceeded(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    writer = TranscriptWriter(path, max_bytes=140)
    writer.write_message(HumanMessage(content="x" * 80))
    writer.write_message(HumanMessage(content="y" * 80))
    writer.close()

    rotated = tmp_path / "session.1.jsonl"
    assert rotated.exists()
    assert [message.content for message in TranscriptReader(path).read_all()] == ["y" * 80]
    assert [message.content for message in TranscriptReader(rotated).read_all()] == ["x" * 80]


def test_list_session_summaries_includes_project_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    path = get_session_path("20260101-000000-abcd")
    writer = TranscriptWriter(path, session_meta={"cwd": "/Users/example/workspace/voice-code"})
    writer.write_message(HumanMessage(content="优化 agent UI 观感"))
    writer.close()

    summaries = list_session_summaries(limit=10, current_session_id="20260101-000000-abcd")

    assert len(summaries) == 1
    assert summaries[0].project_label == "voice-code"
    assert summaries[0].project_path == "/Users/example/workspace/voice-code"
    assert summaries[0].is_current is True


def test_list_session_summaries_collapses_new_wrapper_dir_to_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    path = get_session_path("20260101-000000-abcd")
    writer = TranscriptWriter(path, session_meta={"cwd": "/Users/example/workspace/voice-code"})
    writer.write_message(HumanMessage(content="优化 agent UI 观感"))
    writer.close()

    summaries = list_session_summaries(limit=10)

    assert summaries[0].project_label == "voice-code"
    assert summaries[0].project_path == "/Users/example/workspace/voice-code"


def test_list_session_summaries_uses_human_friendly_legacy_group_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    path = get_session_path("20260101-000000-abcd")
    writer = TranscriptWriter(path)
    writer.write_message(HumanMessage(content="旧会话"))
    writer.close()

    summaries = list_session_summaries(limit=10)

    assert summaries[0].project_label == "历史"
    assert summaries[0].project_path == "__legacy__"


def test_list_session_summaries_prefers_sidecar_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    path = get_session_path("20260101-000000-abcd")
    writer = TranscriptWriter(path, session_meta={"cwd": "/Users/example/workspace/voice-code"})
    writer.write_message(HumanMessage(content="旧标题"))
    writer.close()

    save_session_state(
        build_session_runtime_state(
            session_id="20260101-000000-abcd",
            cwd="/Users/example/workspace/voice-code",
            title="优化 agent UI 观感",
            updated_at="2026-07-02 12:34:56",
        )
    )

    summaries = list_session_summaries(limit=10)

    assert summaries[0].title == "优化 agent UI 观感"
    assert summaries[0].updated_at == "2026-07-02 12:34:56"
    assert summaries[0].project_label == "voice-code"


def test_archive_session_hides_session_without_deleting_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    session_id = "20260101-000000-abcd"
    path = get_session_path(session_id)
    writer = TranscriptWriter(path, session_meta={"cwd": "/Users/example/workspace/voice-code"})
    writer.write_message(HumanMessage(content="需要归档的任务"))
    writer.close()

    result = archive_session(session_id)

    assert result.archived is True
    assert path.exists()
    assert list_session_summaries(limit=10) == []


def test_group_session_summaries_groups_by_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    path1 = get_session_path("20260101-000000-abcd")
    writer1 = TranscriptWriter(path1, session_meta={"cwd": "/Users/example/workspace/voice-code"})
    writer1.write_message(HumanMessage(content="session one"))
    writer1.close()

    path2 = get_session_path("20260101-000001-efgh")
    writer2 = TranscriptWriter(path2, session_meta={"cwd": "/Users/example/workspace/voice-code"})
    writer2.write_message(HumanMessage(content="session two"))
    writer2.close()

    path3 = get_session_path("20260101-000002-ijkl")
    writer3 = TranscriptWriter(path3, session_meta={"cwd": "/Users/example/notes/network"})
    writer3.write_message(HumanMessage(content="session three"))
    writer3.close()

    summaries = list_session_summaries(limit=10)
    groups = group_session_summaries(summaries)

    assert [group.label for group in groups] == ["network", "voice-code"]
    assert [len(group.sessions) for group in groups] == [1, 2]
