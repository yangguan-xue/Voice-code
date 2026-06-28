"""Bash tool — execute shell commands"""

from __future__ import annotations

import asyncio
import os
import platform

from langchain_core.tools import tool

_MAX_OUTPUT_CHARS = 100_000


def _shell() -> str:
    return os.environ.get("SHELL", "bash")


def _timeout_handler():
    """Fallback timeout — deprecated in favor of asyncio.wait_for."""
    pass


async def _run_command(
    command: str, timeout_ms: int, cwd: str | None = None
) -> tuple[str, str, int]:
    """执行命令，返回 (stdout, stderr, returncode)。"""
    shell = _shell()
    timeout_sec = timeout_ms / 1000.0

    if platform.system() == "Windows":
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            shell, "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_sec,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return (
            "",
            f"<tool_use_error>Error: Command timed out after {timeout_ms}ms</tool_use_error>",
            -1,
        )

    out = stdout.decode("utf-8", errors="replace") if stdout else ""
    err = stderr.decode("utf-8", errors="replace") if stderr else ""

    return out, err, proc.returncode or 0


@tool
def bash(command: str, description: str = "", timeout: int = 120000) -> str:
    """Execute a bash command in the persistent shell session with optional timeout.

    IMPORTANT: Do NOT use Bash for file discovery, directory trees, or content
    search when dedicated tools are available. Instead, use the appropriate
    dedicated tool as this will provide a much better experience for the user:
      - File search: use Glob (NOT find or ls)
      - Content search: use Grep (NOT grep or rg)
      - Read files: use FileRead (NOT cat/head/tail)
      - Edit files: use FileEdit (NOT sed/awk)
      - Write files: use FileWrite (NOT echo >/cat <<EOF)
      - Communication: output text directly (NOT echo/printf)
    Reserve Bash exclusively for terminal operations (git, npm, docker, etc.)
    If you are unsure, default to the dedicated tool and only fall back to
    Bash if absolutely necessary.

    Before executing:
    - Directory verification: if the command will create new directories or files,
      first check that the parent directory exists
    - Always quote file paths that contain spaces
    - Use workdir parameter instead of `cd && command` patterns

    Args:
        command: The bash command to execute.
        description: Clear, concise description in 5-10 words.
        timeout: Maximum timeout in milliseconds (default 120000).
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    out, err, rc = loop.run_until_complete(_run_command(command, timeout))

    parts: list[str] = []
    if out:
        parts.append(out)
    if err:
        parts.append(err)

    result = "\n".join(parts).strip() or "(no output)"

    if len(result) > _MAX_OUTPUT_CHARS:
        result = result[:_MAX_OUTPUT_CHARS] + "\n\n... (output truncated)"

    if rc != 0:
        result += f"\n\nExit code: {rc}"

    return result


bash.metadata = {
    "is_readonly": False,
    "is_concurrency_safe": False,
    "max_result_chars": 100_000,
}
