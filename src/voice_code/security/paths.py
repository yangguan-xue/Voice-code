"""Workspace path confinement for model-accessible filesystem tools."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


class WorkspaceBoundaryError(ValueError):
    """Raised when a tool attempts to access outside the active workspace."""


_workspace_root: ContextVar[Path | None] = ContextVar("workspace_root", default=None)


def configure_workspace_root(root: str | Path) -> Path:
    """Set the workspace boundary for the current async execution context."""
    resolved = Path(root).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise WorkspaceBoundaryError(f"Workspace root is not a directory: {resolved}")
    _workspace_root.set(resolved)
    return resolved


def get_workspace_root() -> Path | None:
    return _workspace_root.get()


@contextmanager
def workspace_boundary(root: str | Path) -> Iterator[Path]:
    """Temporarily configure a workspace and restore the prior async context."""
    resolved = Path(root).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise WorkspaceBoundaryError(f"Workspace root is not a directory: {resolved}")
    token = _workspace_root.set(resolved)
    try:
        yield resolved
    finally:
        _workspace_root.reset(token)


def resolve_workspace_path(path: str | Path, *, allow_missing: bool = True) -> Path:
    """Resolve a path and prove that it remains under the configured workspace.

    Direct unit-level tool use remains unconfined until runtime bootstrap sets a
    root. Production entry points always configure the root before exposing tools.
    """
    candidate = Path(path).expanduser()
    root = get_workspace_root()
    if not candidate.is_absolute():
        candidate = (root or Path.cwd()) / candidate
    resolved = candidate.resolve(strict=not allow_missing)
    if root is None or os.getenv("REASONING_ALLOW_OUTSIDE_WORKSPACE") == "1":
        return resolved
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise WorkspaceBoundaryError(
            f"Path is outside the active workspace ({root}): {resolved}"
        ) from exc
    return resolved
