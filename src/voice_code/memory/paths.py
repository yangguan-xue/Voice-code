from __future__ import annotations

import hashlib
import os
from pathlib import Path


def get_reasoning_dir() -> Path:
    return Path.home() / ".reasoning"


def get_memory_root() -> Path:
    return get_reasoning_dir() / "memory"


def get_user_memory_dir() -> Path:
    return get_memory_root() / "user"


def get_user_memory_entries_dir() -> Path:
    return get_user_memory_dir() / "entries"


def get_user_memory_index_path() -> Path:
    return get_user_memory_dir() / "index.db"


def _compute_project_key(project_root: str) -> str:
    normalized = os.path.normpath(os.path.realpath(project_root))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def get_project_memory_key(project_path: str) -> str:
    return _compute_project_key(project_path)


def get_project_memory_dir(project_root: str) -> Path:
    key = _compute_project_key(project_root)
    return get_memory_root() / "projects" / key


def get_project_memory_entries_dir(project_root: str) -> Path:
    return get_project_memory_dir(project_root) / "entries"


def get_project_memory_index_path(project_root: str) -> Path:
    return get_project_memory_dir(project_root) / "index.db"


def get_memory_dir_for_scope(scope: str, project_root: str | None = None) -> Path:
    if scope == "user":
        return get_user_memory_dir()
    elif scope == "project":
        if project_root is None:
            raise ValueError("project_root required for project scope")
        return get_project_memory_dir(project_root)
    else:
        raise ValueError(f"Unknown scope: {scope}")


def get_entries_dir_for_scope(scope: str, project_root: str | None = None) -> Path:
    if scope == "user":
        return get_user_memory_entries_dir()
    elif scope == "project":
        if project_root is None:
            raise ValueError("project_root required for project scope")
        return get_project_memory_entries_dir(project_root)
    else:
        raise ValueError(f"Unknown scope: {scope}")


def get_index_path_for_scope(scope: str, project_root: str | None = None) -> Path:
    if scope == "user":
        return get_user_memory_index_path()
    elif scope == "project":
        if project_root is None:
            raise ValueError("project_root required for project scope")
        return get_project_memory_index_path(project_root)
    else:
        raise ValueError(f"Unknown scope: {scope}")


def ensure_memory_dirs(project_root: str | None = None) -> None:
    get_user_memory_entries_dir().mkdir(parents=True, exist_ok=True)
    _ensure_memory_md(get_user_memory_dir())
    if project_root:
        get_project_memory_entries_dir(project_root).mkdir(parents=True, exist_ok=True)
        _ensure_memory_md(get_project_memory_dir(project_root))


def _ensure_memory_md(dir_path: Path) -> None:
    memory_md = dir_path / "MEMORY.md"
    if not memory_md.exists():
        memory_md.write_text("# Memories\n\n", encoding="utf-8")
