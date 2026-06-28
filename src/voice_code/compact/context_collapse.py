"""ContextCollapse — 折叠旧的纯查询 turn."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from voice_code.compact.stats import CompactionStats
from voice_code.compact.token_count import rough_token_count_for_messages
from voice_code.compact.turn_split import split_into_turns

_WRITE_LIKE_TOOLS = {"write", "edit", "bash"}


def _tool_name(tc: object) -> str:
    if isinstance(tc, dict):
        return str(tc.get("name", "") or "")
    return str(getattr(tc, "name", "") or "")


def _is_write_turn(turn: list[BaseMessage]) -> bool:
    for msg in turn:
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                if _tool_name(tc) in _WRITE_LIKE_TOOLS:
                    return True
    return False


def _summarize_turn(turn: list[BaseMessage], turn_id: int) -> HumanMessage:
    tools_used: list[str] = []
    agent_summary = ""

    for msg in turn:
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                name = _tool_name(tc)
                if name:
                    tools_used.append(name)
            if not agent_summary and isinstance(msg.content, str) and msg.content.strip():
                agent_summary = msg.content.strip()[:120]

    tool_list = ", ".join(tools_used) if tools_used else "?"
    summary = f"[Turn {turn_id}: {tool_list}]"
    if agent_summary:
        summary += f' → "{agent_summary}"'
    return HumanMessage(content=summary)


def collapse_old_turns(
    messages: list[BaseMessage],
    min_turns_before_collapse: int = 10,
) -> tuple[list[BaseMessage], CompactionStats]:
    """Collapse old read-only turns into one-line summaries."""
    prefix, turns = split_into_turns(messages)
    if len(turns) < min_turns_before_collapse:
        return messages, CompactionStats(layer="collapse")

    collapse_before = max(0, len(turns) - min_turns_before_collapse)
    if collapse_before == 0:
        return messages, CompactionStats(layer="collapse")

    result: list[BaseMessage] = list(prefix)
    collapsed_count = 0
    tokens_before = rough_token_count_for_messages(messages)
    for idx, turn in enumerate(turns, start=1):
        if idx <= collapse_before and not _is_write_turn(turn):
            result.append(_summarize_turn(turn, idx))
            collapsed_count += 1
        else:
            result.extend(turn)

    if collapsed_count == 0:
        return messages, CompactionStats(layer="collapse")

    tokens_after = rough_token_count_for_messages(result)
    return result, CompactionStats(
        layer="collapse",
        active=True,
        messages_before=len(messages),
        messages_after=len(result),
        tokens_freed=max(0, tokens_before - tokens_after),
        details=f"{collapsed_count} turns collapsed",
    )
