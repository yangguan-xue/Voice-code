"""Session state and unified resume tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from voice_code.session.manager import get_session_path
from voice_code.session.resume import resume_runtime_session
from voice_code.session.state import (
    CURRENT_SESSION_STATE_SCHEMA_VERSION,
    build_session_runtime_state,
    get_session_state_path,
    load_session_state,
    save_session_state,
)
from voice_code.session.transcript import TranscriptWriter


def test_session_state_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    state = build_session_runtime_state(
        session_id="20260101-000000-abcd",
        cwd="/Users/example/workspace/voice-code",
        title="优化 agent UI 观感",
        agent_mode="default",
        model_name="voice-code-pro",
    )
    save_session_state(state)

    loaded = load_session_state("20260101-000000-abcd")

    assert loaded.source == "loaded"
    assert loaded.state.schema_version == CURRENT_SESSION_STATE_SCHEMA_VERSION
    assert loaded.state.project_label == "voice-code"
    assert loaded.state.title == "优化 agent UI 观感"


def test_load_session_state_missing_file_returns_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    loaded = load_session_state(
        "20260101-000000-abcd",
        fallback_cwd="/Users/example/workspace/voice-code",
        fallback_title="旧会话",
    )

    assert loaded.source == "missing"
    assert loaded.state.project_label == "voice-code"
    assert loaded.state.title == "旧会话"


def test_load_session_state_invalid_file_degrades_to_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)
    get_session_state_path("20260101-000000-abcd").write_text("{oops", encoding="utf-8")

    loaded = load_session_state(
        "20260101-000000-abcd",
        fallback_cwd="/Users/example/workspace/voice-code",
    )

    assert loaded.source == "invalid"
    assert loaded.state.project_label == "voice-code"


def test_resume_runtime_session_loads_sidecar_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    transcript_path = get_session_path("20260101-000000-abcd")
    writer = TranscriptWriter(
        transcript_path,
        session_meta={"cwd": "/Users/example/workspace/voice-code"},
    )
    writer.write_message(HumanMessage(content="优化 agent UI 观感"))
    writer.close()

    save_session_state(
        build_session_runtime_state(
            session_id="20260101-000000-abcd",
            cwd="/Users/example/workspace/voice-code",
            title="优化 agent UI 观感",
            model_name="voice-code-pro",
        )
    )

    resumed = resume_runtime_session("20260101-000000-abcd")

    assert resumed.state_source == "loaded"
    assert resumed.runtime_state.project_label == "voice-code"
    assert resumed.messages[0].content == "优化 agent UI 观感"
    resumed.transcript_writer.close()


def test_resume_runtime_session_legacy_transcript_uses_transcript_fallbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    transcript_path = get_session_path("20260101-000000-abcd")
    writer = TranscriptWriter(
        transcript_path,
        session_meta={"cwd": "/Users/example/workspace/voice-code"},
    )
    writer.write_message(HumanMessage(content="优化 agent UI 观感"))
    writer.close()

    resumed = resume_runtime_session("20260101-000000-abcd")

    assert resumed.state_source == "missing"
    assert resumed.runtime_state.project_label == "voice-code"
    assert resumed.runtime_state.title == "优化 agent UI 观感"
    resumed.transcript_writer.close()
