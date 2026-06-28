"""Glob tool — search for files by glob pattern"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

_MAX_RESULTS = 100
_EXACT_PATH_HINT = "Report the exact missing path and do not assume a similar file or directory."


@tool
def glob(pattern: str, path: str = ".") -> str:
    """Fast file pattern matching tool that works with any codebase size.

    Use this tool when you need to find files by name, wildcard, directory
    structure, or project layout. Prefer Glob over Bash commands like `find`
    or `ls` for file discovery tasks.

    Supports glob patterns like "**/*.js" or "src/**/*.ts".
    Returns matching file paths relative to the search directory.

    Args:
        pattern: The glob pattern to match files against.
        path: Directory to search in (default: current working directory).

    Returns:
        Matching file paths, one per line.
    """
    base = Path(path).expanduser().resolve()

    if not base.exists():
        return (
            "<tool_use_error>Error: Directory not found: "
            f"{path}. {_EXACT_PATH_HINT}</tool_use_error>"
        )

    if not base.is_dir():
        return f"<tool_use_error>Error: Path is not a directory: {path}</tool_use_error>"

    matches: list[str] = []
    for p in base.glob(pattern):
        if matches.__len__() >= _MAX_RESULTS:
            break
        rel = str(p.relative_to(base))
        suffix = "/" if p.is_dir() else ""
        matches.append(rel + suffix)

    if not matches:
        return "No files found"

    result = "\n".join(sorted(matches))

    if len(matches) >= _MAX_RESULTS:
        result += f"\n\n(Results truncated at {_MAX_RESULTS}. Use a more specific pattern or path.)"

    return result


glob.metadata = {
    "is_readonly": True,
    "is_concurrency_safe": True,
    "max_result_chars": 10_000,
}
