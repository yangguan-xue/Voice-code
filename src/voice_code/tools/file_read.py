"""FileRead tool — read file contents with optional offset and limit"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from voice_code.tools.cache import mark_as_read

_MAX_LINES = 2000
_MAX_OUTPUT_CHARS = 50_000
_EXACT_PATH_HINT = "Report the exact missing path and do not assume a similar file or directory."


def _resolve(file_path: str) -> Path:
    return Path(file_path).expanduser().resolve()


@tool
def read(file_path: str, offset: int = 1, limit: int = 2000) -> str:
    """Read a file from the local filesystem.

    Returns the content of a file with line numbers. Supports reading images
    and PDFs (returned as file attachments).

    Args:
        file_path: Absolute path to the file to read.
        offset: Line number to start reading from (1-indexed, default 1).
        limit: Maximum number of lines to read (default 2000).

    Returns:
        The file content with line numbers in the format:
            <line>: <content>
    """
    path = _resolve(file_path)

    if not path.exists():
        return (
            "<tool_use_error>Error: File not found: "
            f"{file_path}. {_EXACT_PATH_HINT}</tool_use_error>"
        )

    if path.is_dir():
        entries = "\n".join(
            f"{e.name}{'/' if e.is_dir() else ''}" for e in sorted(path.iterdir())
        )
        return f"Contents of directory {file_path}:\n{entries}"

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"<tool_use_error>Error reading file: {e}</tool_use_error>"

    mark_as_read(file_path)

    lines = content.split("\n")
    total_lines = len(lines)

    # Clamp offset/limit
    offset = max(1, offset)
    limit = min(limit, _MAX_LINES)

    start = offset - 1
    end = start + limit
    selected = lines[start:end]

    # Add line numbers
    numbered = "\n".join(
        f"{i + offset}: {line}" for i, line in enumerate(selected)
    )

    # Truncate
    if len(numbered) > _MAX_OUTPUT_CHARS:
        numbered = numbered[:_MAX_OUTPUT_CHARS] + "\n... (content truncated)"

    footer = (
        f"<system-reminder>Read {len(selected)} lines from {file_path}"
        f" (offset={offset}, total lines={total_lines})</system-reminder>"
    )
    return f"{numbered}\n\n{footer}"


read.metadata = {
    "is_readonly": True,
    "is_concurrency_safe": True,
    "max_result_chars": 50_000,
}
