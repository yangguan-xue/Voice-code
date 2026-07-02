"""Session runtime state sidecar persistence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from voice_code.session.manager import get_session_path

CURRENT_SESSION_STATE_SCHEMA_VERSION = 1
SessionStateSource = Literal["loaded", "missing", "invalid"]


def _utc_now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _derive_project_path(cwd: str) -> str:
    raw = cwd.strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.name == "new" and path.parent.name:
        return str(path.parent)
    if path.name == "src" and path.parent.name == "new" and path.parent.parent.name:
        return str(path.parent.parent)
    return str(path)


def _derive_project_label(project_path: str) -> str:
    normalized = project_path.strip()
    if not normalized:
        return "历史"
    return Path(normalized).name.strip() or "历史"


@dataclass(slots=True)
class SessionRuntimeState:
    session_id: str
    schema_version: int = CURRENT_SESSION_STATE_SCHEMA_VERSION
    cwd: str = ""
    project_path: str = ""
    project_label: str = ""
    created_at: str = ""
    updated_at: str = ""
    title: str = ""
    agent_mode: str = ""
    model_name: str = ""
    todo_state: dict[str, Any] = field(default_factory=dict)
    task_state: dict[str, Any] = field(default_factory=dict)
    subagent_notifications: list[dict[str, Any]] = field(default_factory=list)
    compact_state: dict[str, Any] = field(default_factory=dict)
    ui_state: dict[str, Any] = field(default_factory=dict)
    permission_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SessionStateLoadResult:
    state: SessionRuntimeState
    source: SessionStateSource


def get_session_state_path(session_id: str) -> Path:
    return get_session_path(session_id).with_suffix(".state.json")


def build_session_runtime_state(
    *,
    session_id: str,
    cwd: str = "",
    created_at: str | None = None,
    updated_at: str | None = None,
    title: str = "",
    agent_mode: str = "",
    model_name: str = "",
) -> SessionRuntimeState:
    now = _utc_now_text()
    project_path = _derive_project_path(cwd)
    return SessionRuntimeState(
        session_id=session_id,
        cwd=cwd,
        project_path=project_path,
        project_label=_derive_project_label(project_path),
        created_at=created_at or now,
        updated_at=updated_at or created_at or now,
        title=title,
        agent_mode=agent_mode,
        model_name=model_name,
    )


def save_session_state(state: SessionRuntimeState) -> None:
    path = get_session_state_path(state.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_session_state(
    session_id: str,
    *,
    fallback_cwd: str = "",
    fallback_title: str = "",
    fallback_updated_at: str = "",
) -> SessionStateLoadResult:
    path = get_session_state_path(session_id)
    fallback_state = build_session_runtime_state(
        session_id=session_id,
        cwd=fallback_cwd,
        updated_at=fallback_updated_at or None,
        title=fallback_title,
    )
    if not path.exists():
        return SessionStateLoadResult(state=fallback_state, source="missing")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SessionStateLoadResult(state=fallback_state, source="invalid")

    if not isinstance(payload, dict):
        return SessionStateLoadResult(state=fallback_state, source="invalid")

    cwd = str(payload.get("cwd", "")).strip() or fallback_cwd
    project_path = str(payload.get("project_path", "")).strip() or _derive_project_path(cwd)
    project_label = (
        str(payload.get("project_label", "")).strip() or _derive_project_label(project_path)
    )
    created_at = str(payload.get("created_at", "")).strip() or fallback_state.created_at
    updated_at = str(payload.get("updated_at", "")).strip() or (
        fallback_updated_at or created_at
    )
    title = str(payload.get("title", "")).strip() or fallback_title

    state = SessionRuntimeState(
        session_id=session_id,
        schema_version=int(payload.get("schema_version", CURRENT_SESSION_STATE_SCHEMA_VERSION)),
        cwd=cwd,
        project_path=project_path,
        project_label=project_label,
        created_at=created_at,
        updated_at=updated_at,
        title=title,
        agent_mode=str(payload.get("agent_mode", "")).strip(),
        model_name=str(payload.get("model_name", "")).strip(),
        todo_state=dict(payload.get("todo_state", {}) or {}),
        task_state=dict(payload.get("task_state", {}) or {}),
        subagent_notifications=list(payload.get("subagent_notifications", []) or []),
        compact_state=dict(payload.get("compact_state", {}) or {}),
        ui_state=dict(payload.get("ui_state", {}) or {}),
        permission_state=dict(payload.get("permission_state", {}) or {}),
    )
    return SessionStateLoadResult(state=state, source="loaded")
