"""List subagent tasks."""

from __future__ import annotations

from langchain_core.tools import tool

from voice_code.subagents.service import get_current_runtime_context


@tool
def task_list() -> str:
    """List current subagent tasks for this session."""
    context = get_current_runtime_context()
    return context.service.list_tasks_text()


task_list.metadata = {
    "is_readonly": True,
    "is_concurrency_safe": True,
}
