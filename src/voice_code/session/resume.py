"""Unified session resume service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from langchain_core.messages import BaseMessage

from voice_code.session.manager import get_session_path
from voice_code.session.state import (
    SessionRuntimeState,
    SessionStateSource,
    build_session_runtime_state,
    load_session_state,
)
from voice_code.session.transcript import TranscriptReader, TranscriptWriter


@dataclass(slots=True)
class ResumeRuntimeResult:
    session_id: str
    messages: list[BaseMessage]
    transcript_writer: TranscriptWriter
    runtime_state: SessionRuntimeState = field(
        default_factory=lambda: build_session_runtime_state(session_id="")
    )
    state_source: SessionStateSource = "missing"

    def __post_init__(self) -> None:
        if not self.runtime_state.session_id:
            self.runtime_state.session_id = self.session_id


def resume_runtime_session(session_id: str) -> ResumeRuntimeResult:
    path = get_session_path(session_id)
    if not path.exists():
        raise FileNotFoundError(session_id)

    reader = TranscriptReader(path)
    info = reader.read_info()
    updated_at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    state_result = load_session_state(
        session_id,
        fallback_cwd=str(info.get("cwd", "")),
        fallback_title=str(info.get("title", "")),
        fallback_updated_at=updated_at,
    )
    return ResumeRuntimeResult(
        session_id=session_id,
        messages=reader.read_all(),
        transcript_writer=TranscriptWriter(path),
        runtime_state=state_result.state,
        state_source=state_result.source,
    )
