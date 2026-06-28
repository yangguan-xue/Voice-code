"""TodoWrite tool — agent 任务列表追踪"""

from __future__ import annotations

from langchain_core.tools import tool

_tasks: list[str] = []


@tool
def todo_write(tasks: str) -> str:
    """Create and manage a structured task list for your current coding session.

    Use this tool to break down and track your work. Each task has a status:
    in_progress, pending, completed, or cancelled. Only one task can be
    in_progress at a time.

    Format each task as:
    - [status] Task description

    Args:
        tasks: The task list in markdown format, one task per line.
               Example:
               - [in_progress] Add login page
               - [pending] Add dashboard
               - [completed] Set up project
    """
    lines = tasks.strip().split("\n")
    parsed: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- ["):
            parsed.append(stripped)

    if parsed:
        _tasks.clear()
        _tasks.extend(parsed)

    if not _tasks:
        return "No tasks provided."

    result = "Tasks:\n" + "\n".join(_tasks)
    in_progress = [t for t in _tasks if "[in_progress]" in t]
    if len(in_progress) > 1:
        result += "\n\n⚠ Multiple tasks marked in_progress. Keep only one."
    return result


todo_write.metadata = {
    "is_readonly": False,
    "is_concurrency_safe": False,
}
