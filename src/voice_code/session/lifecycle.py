from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voice_code.memory.repository import MemoryRepository
from voice_code.session.manager import get_session_path
from voice_code.session.state import (
    get_session_state_path,
    load_session_state,
    save_session_state,
)
from voice_code.session.transcript import TranscriptReader


@dataclass(frozen=True, slots=True)
class SessionDeleteResult:
    session_id: str
    transcript_deleted: bool
    state_deleted: bool
    memory_deleted: int
    evidence_deleted: int
    extraction_deleted: int
    candidate_deleted: int


@dataclass(frozen=True, slots=True)
class SessionArchiveResult:
    session_id: str
    archived: bool


def export_session(
    session_id: str,
    *,
    memory_repository: MemoryRepository | None = None,
    user_id: str = "local",
) -> dict[str, Any]:
    transcript_path = get_session_path(session_id)
    state_result = load_session_state(session_id)
    memory_records = (
        memory_repository.export_session_records(session_id=session_id, user_id=user_id)
        if memory_repository is not None
        else []
    )
    return {
        "session_id": session_id,
        "transcript": _export_transcript(transcript_path),
        "state": state_result.state.to_dict(),
        "state_source": state_result.source,
        "task": dict(state_result.state.task_state),
        "memory": memory_records,
        "evidence": [
            evidence
            for memory in memory_records
            for evidence in list(memory.get("evidence", []) or [])
        ],
    }


def archive_session(session_id: str) -> SessionArchiveResult:
    transcript_path = get_session_path(session_id)
    if not transcript_path.exists():
        return SessionArchiveResult(session_id=session_id, archived=False)
    state_result = load_session_state(session_id)
    state = state_result.state
    state.ui_state = {**state.ui_state, "archived": True}
    save_session_state(state)
    return SessionArchiveResult(session_id=session_id, archived=True)


def delete_session(
    session_id: str,
    *,
    memory_repository: MemoryRepository | None = None,
    user_id: str = "local",
) -> SessionDeleteResult:
    transcript_deleted = _unlink_if_exists(get_session_path(session_id))
    state_deleted = _unlink_if_exists(get_session_state_path(session_id))
    deleted = (
        memory_repository.delete_session_records(session_id=session_id, user_id=user_id)
        if memory_repository is not None
        else {}
    )
    return SessionDeleteResult(
        session_id=session_id,
        transcript_deleted=transcript_deleted,
        state_deleted=state_deleted,
        memory_deleted=int(deleted.get("memory_deleted", 0)),
        evidence_deleted=int(deleted.get("evidence_deleted", 0)),
        extraction_deleted=int(deleted.get("extraction_deleted", 0)),
        candidate_deleted=int(deleted.get("candidate_deleted", 0)),
    )


def _export_transcript(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "records": [], "corrupt_record_count": 0}
    records = TranscriptReader(path).read_records()
    return {
        "path": str(path),
        "records": records.records,
        "corrupt_record_count": records.corrupt_record_count,
    }


def _unlink_if_exists(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
