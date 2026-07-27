"""Security boundaries shared by tools, persistence, and runtime setup."""

from voice_code.security.paths import (
    WorkspaceBoundaryError,
    configure_workspace_root,
    get_workspace_root,
    resolve_workspace_path,
    workspace_boundary,
)
from voice_code.security.redaction import redact_secrets

__all__ = [
    "WorkspaceBoundaryError",
    "configure_workspace_root",
    "get_workspace_root",
    "redact_secrets",
    "resolve_workspace_path",
    "workspace_boundary",
]
