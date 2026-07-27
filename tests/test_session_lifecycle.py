from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.repository import MemoryRepository
from voice_code.session.lifecycle import delete_session, export_session
from voice_code.session.manager import get_session_path
from voice_code.session.state import (
    build_session_runtime_state,
    get_session_state_path,
    save_session_state,
)
from voice_code.session.transcript import TranscriptWriter


def test_export_session_includes_transcript_state_task_memory_and_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)
    session_id = "20260720-120000-abcd"
    writer = TranscriptWriter(get_session_path(session_id), session_meta={"cwd": str(tmp_path)})
    writer.write_message(HumanMessage(content="记住我喜欢简洁回答"))
    writer.close()
    state = build_session_runtime_state(session_id=session_id, cwd=str(tmp_path), title="偏好")
    state.task_state = {"tasks": [{"task_id": "task-1", "status": "completed"}]}
    save_session_state(state)
    repository = MemoryRepository(tmp_path / "memory.db")
    memory = repository.remember(
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content="回答保持简洁。",
        source_session_id=session_id,
    ).entry

    payload = export_session(session_id, memory_repository=repository, user_id="local")

    assert payload["session_id"] == session_id
    assert payload["transcript"]["records"][1]["content"] == "记住我喜欢简洁回答"
    assert payload["state"]["title"] == "偏好"
    assert payload["task"] == {"tasks": [{"task_id": "task-1", "status": "completed"}]}
    assert payload["memory"][0]["memory_id"] == memory.id
    assert payload["evidence"] == []


def test_delete_session_cascades_transcript_state_task_memory_and_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)
    session_id = "20260720-120000-abcd"
    writer = TranscriptWriter(get_session_path(session_id), session_meta={"cwd": str(tmp_path)})
    writer.write_message(HumanMessage(content="记住偏好"))
    writer.close()
    state = build_session_runtime_state(session_id=session_id, cwd=str(tmp_path), title="偏好")
    state.task_state = {"tasks": [{"task_id": "task-1", "transcript_path": "sub.jsonl"}]}
    save_session_state(state)
    repository = MemoryRepository(tmp_path / "memory.db")
    memory = repository.remember(
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content="回答保持简洁。",
        source_session_id=session_id,
    ).entry

    result = delete_session(session_id, memory_repository=repository, user_id="local")

    assert result.transcript_deleted is True
    assert result.state_deleted is True
    assert result.memory_deleted == 1
    assert result.evidence_deleted == 0
    assert not get_session_path(session_id).exists()
    assert not get_session_state_path(session_id).exists()
    assert repository.get(memory.id, user_id="local") is None
    assert repository.export_session_records(session_id=session_id, user_id="local") == []
