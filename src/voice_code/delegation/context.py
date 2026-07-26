"""Collect bounded repository context for terse delegation requests."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path


async def _git(workspace: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8", errors="replace")[-20_000:] if proc.returncode == 0 else ""


async def collect_delegation_context(workspace: str | Path) -> dict[str, object]:
    root = Path(workspace).resolve()
    branch, status, diff = await asyncio.gather(
        _git(root, "branch", "--show-current"),
        _git(root, "status", "--short"),
        _git(root, "diff", "--stat"),
    )
    active_file = os.getenv("REASONING_ACTIVE_FILE", "").strip()
    terminal_excerpt = os.getenv("REASONING_RECENT_TERMINAL", "")[-8000:]
    return {
        "workspace": str(root),
        "branch": branch.strip(),
        "git_status": status,
        "git_diff_stat": diff,
        "active_file": active_file,
        "recent_terminal": terminal_excerpt,
    }
