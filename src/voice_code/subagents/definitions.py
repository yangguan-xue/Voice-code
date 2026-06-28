"""子 agent definitions 与工具过滤。"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.tools import BaseTool


@dataclass(slots=True)
class AgentDefinition:
    agent_type: str
    when_to_use: str
    system_prompt: str
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    model: str | None = None
    max_turns: int | None = None
    background: bool = False


_DEFINITIONS: dict[str, AgentDefinition] = {
    "general-purpose": AgentDefinition(
        agent_type="general-purpose",
        when_to_use="通用子任务执行",
        system_prompt="你是一个通用子 agent。独立完成被委派的任务，简洁汇报事实与结论。",
    ),
    "researcher": AgentDefinition(
        agent_type="researcher",
        when_to_use="信息检索、代码调查、范围摸排",
        system_prompt="你是一个研究型子 agent。优先调查、总结证据、给出明确发现，不做无关改动。",
    ),
    "implementer": AgentDefinition(
        agent_type="implementer",
        when_to_use="定向代码实现与修改",
        system_prompt=(
            "你是一个实现型子 agent。根据任务描述进行实现，"
            "必要时读写代码并简洁汇报结果。"
        ),
    ),
    "reviewer": AgentDefinition(
        agent_type="reviewer",
        when_to_use="代码审查与风险识别",
        system_prompt="你是一个审查型子 agent。优先发现问题、风险和回归，不做无关实现。",
        disallowed_tools=["write", "edit"],
    ),
}


def list_agent_definitions() -> list[AgentDefinition]:
    return list(_DEFINITIONS.values())


def get_agent_definition(agent_type: str) -> AgentDefinition:
    try:
        return _DEFINITIONS[agent_type]
    except KeyError as exc:
        available = ", ".join(sorted(_DEFINITIONS))
        raise ValueError(f"Unknown subagent_type: {agent_type}. Available: {available}") from exc


def filter_tools_for_definition(
    tools: list[BaseTool],
    definition: AgentDefinition,
) -> list[BaseTool]:
    if definition.allowed_tools is not None:
        allowed = set(definition.allowed_tools)
        return [tool for tool in tools if tool.name in allowed]
    if definition.disallowed_tools is not None:
        denied = set(definition.disallowed_tools)
        return [tool for tool in tools if tool.name not in denied]
    return list(tools)
