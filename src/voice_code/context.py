"""上下文管理 — CLAUDE.md 加载 + git 状态快照"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_STATUS_CHARS = 2000

_CLAUDE_MD_FILES = [
    ("CLAUDE.md", "project instructions"),
    (".claude/CLAUDE.md", "project instructions"),
    ("CLAUDE.local.md", "local instructions"),
]


async def _run_git(args: list[str], cwd: str) -> str | None:
    """执行 git 命令，失败返回 None。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd or None,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return stdout.decode("utf-8", errors="replace").strip()
    except (FileNotFoundError, OSError):
        return None


async def _is_git_repo(cwd: str) -> bool:
    """检查目录是否在 git 仓库中。"""
    result = await _run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    return result == "true"


async def get_git_status(cwd: str = "") -> str | None:
    """获取 git 状态快照。

    Returns:
        格式化的 git status 字符串，非 git 仓库返回 None。
    """
    if not await _is_git_repo(cwd):
        return None

    branch, status_short, recent_log = await asyncio.gather(
        _run_git(["branch", "--show-current"], cwd),
        _run_git(["status", "--short"], cwd),
        _run_git(["log", "--oneline", "-n", "5"], cwd),
    )

    branch = _safe(branch, "HEAD")
    status_short = _safe(status_short, "")
    recent_log = _safe(recent_log, "")

    if len(status_short) > _MAX_STATUS_CHARS:
        status_short = (
            status_short[:_MAX_STATUS_CHARS]
            + "\n... (truncated, use Bash tool run 'git status' for full output)"
        )

    status_display = status_short if status_short else "(clean)"

    return (
        "This is the git status at the start of the conversation. Note that this status "
        "is a snapshot in time, and will not update during the conversation.\n"
        f"\nCurrent branch: {branch}\n"
        f"\nStatus:\n{status_display}\n"
        f"\nRecent commits:\n{recent_log}"
    )


async def load_claude_md(cwd: str = "") -> str:
    """加载当前目录的 CLAUDE.md 文件。

    按优先级加载并拼接:
      - CLAUDE.md
      - .claude/CLAUDE.md
      - CLAUDE.local.md
    """
    base = Path(cwd) if cwd else Path.cwd()
    parts: list[str] = []

    for rel_path, desc in _CLAUDE_MD_FILES:
        full_path = base / rel_path
        if not full_path.is_file():
            continue
        try:
            content = full_path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(
                    f"Contents of {full_path} ({desc}):\n\n{content}"
                )
        except OSError as e:
            logger.warning("Failed to read %s: %s", full_path, e)

    return "\n\n".join(parts)


async def get_context(cwd: str = "") -> dict[str, str]:
    """获取完整上下文。

    Returns:
        {"claudeMd": str, "gitStatus": str, "currentDate": str}
    """
    import datetime

    claude_md, git_status = await asyncio.gather(
        load_claude_md(cwd),
        get_git_status(cwd),
    )

    return {
        "claudeMd": claude_md,
        "gitStatus": git_status or "",
        "currentDate": f"Today's date is {datetime.date.today().isoformat()}.",
    }


def _safe(value: str | None, default: str) -> str:
    """空值回退。"""
    return value if value else default
