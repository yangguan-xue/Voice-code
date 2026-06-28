"""Stop subagent tasks."""

from __future__ import annotations

from langchain_core.tools import tool

from voice_code.subagents.service import get_current_runtime_context


@tool
def task_stop(task_id: str = "", stop_all: bool = False) -> str:
    """Stop a running subagent task or all running subagent tasks.

    Args:
        task_id: The task id to stop.
        stop_all: Stop all running tasks when true.
    """
    context = get_current_runtime_context()
    if stop_all:
        return context.service.stop_all_tasks()
    if not task_id.strip():
        return "Provide task_id or set stop_all=true."
    return context.service.stop_task(task_id.strip())


task_stop.metadata = {
    "is_readonly": False,
    "is_concurrency_safe": False,
}
