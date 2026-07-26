"""Cross-platform filesystem helpers."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def best_effort_private_permissions(path: Path, *, directory: bool = False) -> None:
    """Apply private POSIX-style permissions where supported."""
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        logger.debug(
            "private permission chmod skipped",
            extra={"event": "fs.permission.skipped"},
        )
