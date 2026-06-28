"""Get subagent task details."""

from __future__ import annotations

from langchain_core.tools import tool

from voice_code.subagents.service import get_current_runtime_context


@tool
def task_get(task_id: str) -> str:
    """Get details for a subagent task by task id."""
    context = get_current_runtime_context()
    return context.service.get_task_text(task_id)


task_get.metadata = {
    "is_readonly": True,
    "is_concurrency_safe": True,
}
