"""Grep tool — search file contents using regular expressions"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from langchain_core.tools import tool

_MAX_OUTPUT_LINES = 500
_EXACT_PATH_HINT = "Report the exact missing path and do not assume a similar file or directory."


def _find_rg() -> str | None:
    """查找 ripgrep 可执行文件。"""
    rg = shutil.which("rg")
    return rg


async def _run_rg(args: list[str], cwd: str) -> tuple[str, int]:
    """执行 ripgrep 命令。"""
    proc = await asyncio.create_subprocess_exec(
        args[0], *args[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or None,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode("utf-8", errors="replace") if stdout else ""
    err = stderr.decode("utf-8", errors="replace") if stderr else ""
    if err and proc.returncode not in (0, 1):
        return err, proc.returncode or 1
    return out, proc.returncode or 0


@tool
def grep(
    pattern: str,
    path: str = ".",
    include: str = "",
    ignore_case: bool = False,
    head_limit: int = 250,
) -> str:
    """Fast content search tool that works with any codebase size.

    ALWAYS use this tool for content search tasks. Do NOT invoke `grep` or
    `rg` through Bash for normal codebase searches.

    Searches file contents using regular expressions.
    Requires ripgrep (rg) to be installed.

    Args:
        pattern: Regular expression pattern to search for.
        path: File or directory to search in (default: current working directory).
        include: Glob pattern to filter files (e.g. "*.py", "*.{ts,tsx}").
        ignore_case: Case-insensitive search (default: False).
        head_limit: Maximum number of matching lines to return (default: 250, 0 = unlimited).

    Returns:
        Matching lines with file paths and line numbers.
    """
    rg = _find_rg()
    if not rg:
        return (
            "<tool_use_error>Error: ripgrep (rg) is not installed."
            " Please install it to use Grep.</tool_use_error>"
        )

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return (
            "<tool_use_error>Error: Path not found: "
            f"{path}. {_EXACT_PATH_HINT}</tool_use_error>"
        )

    args: list[str] = [
        rg,
        "--no-heading",
        "--line-number",
        "--color", "never",
        "--no-ignore-vcs",
        "--hidden",
        "--max-columns", "500",
    ]

    if ignore_case:
        args.append("--ignore-case")

    if include:
        for g in include.replace(",", " ").split():
            g = g.strip()
            if g:
                args.extend(["--glob", g])

    if head_limit > 0:
        args.extend(["-m", str(head_limit)])

    args.append(pattern)
    args.append(str(target))

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    output, rc = loop.run_until_complete(_run_rg(args, str(Path.cwd())))

    if rc == 1 and not output:
        return "No matches found"

    lines = output.strip().split("\n") if output.strip() else []
    if len(lines) > _MAX_OUTPUT_LINES:
        lines = lines[:_MAX_OUTPUT_LINES]
        output = "\n".join(lines) + f"\n\n... (output truncated at {_MAX_OUTPUT_LINES} lines)"

    return output if output.strip() else "No matches found"


grep.metadata = {
    "is_readonly": True,
    "is_concurrency_safe": True,
    "max_result_chars": 30_000,
}
