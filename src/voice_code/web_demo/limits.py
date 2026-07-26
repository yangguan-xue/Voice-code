"""Resource limit defaults for the web demo V0."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebDemoLimits:
    session_ttl_seconds: int = 30 * 60
    max_active_sessions: int = 20
    turn_timeout_seconds: int = 5 * 60
    permission_timeout_seconds: float = 300.0
    max_patch_chars: int = 50_000
    max_session_creates_per_minute: int = 10
    max_workspace_bytes: int = 2 * 1024 * 1024
    max_workspace_files: int = 200
    max_file_bytes: int = 256 * 1024
    max_download_bytes: int = 256 * 1024
