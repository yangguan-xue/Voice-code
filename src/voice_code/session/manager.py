"""Transcript session manager."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from voice_code.session.transcript import TranscriptReader

_LEGACY_PROJECT_KEY = "__legacy__"


@dataclass(frozen=True)
class SessionSummary:
    id: str
    title: str
    message_count: int
    updated_at: str
    project_label: str
    project_path: str
    is_current: bool = False


@dataclass(frozen=True)
class SessionGroup:
    key: str
    label: str
    project_path: str
    sessions: list[SessionSummary] = field(default_factory=list)


def get_transcript_dir() -> Path:
    return Path.home() / ".reasoning" / "transcripts"


def make_session_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    return f"{timestamp}-{suffix}"


def get_session_path(session_id: str) -> Path:
    return get_transcript_dir() / f"{session_id}.jsonl"


def _normalize_project_path(project_path: str) -> str:
    raw_path = project_path.strip()
    if not raw_path:
        return ""
    if raw_path.startswith("/"):
        path = PurePosixPath(raw_path)
        if path.name == "new" and path.parent.name:
            return str(path.parent)
        if path.name == "src" and path.parent.name == "new" and path.parent.parent.name:
            return str(path.parent.parent)
        return str(path)
    path = Path(raw_path)
    # The published app currently runs from the repo's `new/` package dir,
    # but users think in terms of the workspace root (`reasoning`), not `new`.
    if path.name == "new" and path.parent.name:
        return str(path.parent)
    if path.name == "src" and path.parent.name == "new" and path.parent.parent.name:
        return str(path.parent.parent)
    return str(path)


def _project_label_from_path(project_path: str) -> str:
    if not project_path.strip():
        return "历史"
    name = Path(project_path).name.strip()
    return name or "历史"


def list_session_summaries(
    *,
    limit: int = 20,
    current_session_id: str | None = None,
) -> list[SessionSummary]:
    from voice_code.session.state import load_session_state

    transcript_dir = get_transcript_dir()
    if not transcript_dir.exists():
        return []

    files = sorted(transcript_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    results: list[SessionSummary] = []
    for path in files[:limit]:
        reader = TranscriptReader(path)
        try:
            info = reader.read_info()
        except OSError:
            continue
        if int(info.get("message_count", 0)) == 0 and int(info.get("corrupt_record_count", 0)) > 0:
            continue
        updated_at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        state_result = load_session_state(
            path.stem,
            fallback_cwd=str(info.get("cwd", "")),
            fallback_title=str(info.get("title", "")),
            fallback_updated_at=updated_at,
        )
        runtime_state = state_result.state
        if bool(runtime_state.ui_state.get("archived", False)):
            continue
        title = runtime_state.title.strip() or str(info["title"])
        project_path = _normalize_project_path(runtime_state.project_path or runtime_state.cwd)
        summary_updated_at = runtime_state.updated_at.strip() or updated_at
        results.append(
            SessionSummary(
                id=path.stem,
                title=title,
                message_count=int(info["message_count"]),
                updated_at=summary_updated_at,
                project_label=_project_label_from_path(project_path),
                project_path=project_path or _LEGACY_PROJECT_KEY,
                is_current=path.stem == current_session_id,
            )
        )
    return results


def group_session_summaries(
    sessions: list[SessionSummary],
) -> list[SessionGroup]:
    ordered_groups: dict[str, list[SessionSummary]] = {}
    for session in sessions:
        key = session.project_path
        ordered_groups.setdefault(key, []).append(session)

    groups: list[SessionGroup] = []
    for key, grouped_sessions in ordered_groups.items():
        groups.append(
            SessionGroup(
                key=key,
                label=grouped_sessions[0].project_label,
                project_path=key,
                sessions=grouped_sessions,
            )
        )
    return groups


def list_sessions(limit: int = 20) -> list[dict[str, str | int]]:
    return [
        {
            "id": session.id,
            "title": session.title,
            "message_count": session.message_count,
            "created_at": session.updated_at,
        }
        for session in list_session_summaries(limit=limit)
    ]


def clear_all_sessions() -> int:
    transcript_dir = get_transcript_dir()
    if not transcript_dir.exists():
        return 0

    count = 0
    for path in transcript_dir.glob("*.jsonl"):
        path.unlink()
        count += 1
    return count
