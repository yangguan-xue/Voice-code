"""Session utilities."""

from voice_code.session.lifecycle import SessionDeleteResult, delete_session, export_session
from voice_code.session.manager import (
    SessionGroup,
    SessionSummary,
    get_session_path,
    get_transcript_dir,
    group_session_summaries,
    list_session_summaries,
    list_sessions,
    make_session_id,
)
from voice_code.session.resume import ResumeRuntimeResult, resume_runtime_session
from voice_code.session.state import (
    CURRENT_SESSION_STATE_SCHEMA_VERSION,
    SessionRuntimeState,
    SessionStateLoadResult,
    build_session_runtime_state,
    get_session_state_path,
    load_session_state,
    save_session_state,
)
from voice_code.session.transcript import TranscriptReader, TranscriptWriter

__all__ = [
    "SessionDeleteResult",
    "delete_session",
    "export_session",
    "TranscriptReader",
    "TranscriptWriter",
    "ResumeRuntimeResult",
    "resume_runtime_session",
    "SessionGroup",
    "SessionSummary",
    "SessionRuntimeState",
    "SessionStateLoadResult",
    "CURRENT_SESSION_STATE_SCHEMA_VERSION",
    "get_transcript_dir",
    "make_session_id",
    "get_session_path",
    "get_session_state_path",
    "build_session_runtime_state",
    "load_session_state",
    "save_session_state",
    "list_sessions",
    "list_session_summaries",
    "group_session_summaries",
]
