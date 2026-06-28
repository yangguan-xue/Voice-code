"""Cross-platform clipboard copy."""

from __future__ import annotations

import subprocess
import sys


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    if not text:
        return False

    if sys.platform == "darwin":
        try:
            subprocess.run(["pbcopy"], input=text, text=True, check=True, timeout=2)
            return True
        except (subprocess.SubprocessError, OSError):
            return False

    if sys.platform == "linux":
        for cmd in (["xclip", "-selection", "clipboard"], ["wl-copy"]):
            try:
                subprocess.run(cmd, input=text, text=True, check=True, timeout=2)
                return True
            except (FileNotFoundError, subprocess.SubprocessError, OSError):
                continue
        return False

    return False
