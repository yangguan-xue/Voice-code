"""统一 AgentTool。"""

from __future__ import annotations

from langchain_core.tools import tool

from voice_code.subagents.planner import AgentToolRequest
from voice_code.subagents.service import get_current_runtime_context


@tool
def agent(
    description: str,
    prompt: str,
    subagent_type: str | None = None,
    model: str | None = None,
    run_in_background: bool | None = None,
    count: int = 1,
) -> str:
    """Launch a subagent to handle a delegated task.

    Args:
        description: Short description for the task.
        prompt: The delegated task prompt.
        subagent_type: Optional specialized subagent type. Omit to fork.
        model: Optional model override for the subagent.
        run_in_background: Whether to run asynchronously in the background.
        count: Number of subagents to launch in parallel.
    """
    context = get_current_runtime_context()
    return context.service.invoke_agent_tool(
        AgentToolRequest(
            description=description,
            prompt=prompt,
            subagent_type=subagent_type,
            model=model,
            run_in_background=run_in_background,
            count=count,
        )
    )


agent.metadata = {
    "is_readonly": False,
    "is_concurrency_safe": False,
}
