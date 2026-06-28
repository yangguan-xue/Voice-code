"""SNIP — 删除旧工具结果释放 token."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from reasoning_agent.compact.stats import CompactionStats
from reasoning_agent.compact.token_count import rough_token_count_for_messages
from reasoning_agent.compact.turn_split import split_into_turns

_SNIP_BOUNDARY_PREFIX = "[SNIP:"


def _cleanup_orphaned_tool_calls(
    messages: list[BaseMessage],
    removed_tool_ids: set[str],
) -> list[BaseMessage]:
    """移除 AIMessage 中已被删除的 tool_call 条目，避免 API 报 insufficient tool messages。"""
    if not removed_tool_ids:
        return messages

    result: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            surviving: list[object] = []
            orphaned_count = 0
            for tc in msg.tool_calls:
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                if str(tc_id) in removed_tool_ids:
                    orphaned_count += 1
                else:
                    surviving.append(tc)
            if orphaned_count > 0:
                new_msg = AIMessage(
                    content=msg.content,
                    tool_calls=surviving if surviving else [],
                )
                # Preserve additional_kwargs (DeepSeek reasoning_content)
                if msg.additional_kwargs:
                    new_msg.additional_kwargs = msg.additional_kwargs
                result.append(new_msg)
                continue
        result.append(msg)
    return result


def snip_compact(
    messages: list[BaseMessage],
    keep_recent_turns: int = 3,
) -> tuple[list[BaseMessage], CompactionStats]:
    """删除旧轮次的 ToolMessage，同时清理对应 AIMessage 中的 tool_call 引用。"""
    if keep_recent_turns <= 0:
        keep_recent_turns = 0

    prefix, turns = split_into_turns(messages)
    if not turns:
        return messages, CompactionStats(layer="snip")

    if len(turns) <= keep_recent_turns:
        return messages, CompactionStats(layer="snip")

    index_lookup = {id(msg): idx for idx, msg in enumerate(messages)}
    old_turns = turns[:-keep_recent_turns]
    remove_indices: list[int] = []
    removed_tool_ids: set[str] = set()
    tokens_freed = 0

    for turn in old_turns:
        for msg in turn:
            if isinstance(msg, ToolMessage):
                remove_indices.append(index_lookup[id(msg)])
                removed_tool_ids.add(str(msg.tool_call_id))
                tokens_freed += max(1, rough_token_count_for_messages([msg]))

    if not remove_indices:
        return messages, CompactionStats(layer="snip")

    remove_set = set(remove_indices)
    boundary_index = min(remove_indices)
    removed_count = len(remove_indices)
    boundary = HumanMessage(
        content=(
            f"{_SNIP_BOUNDARY_PREFIX} {removed_count} old tool results removed, "
            f"~{tokens_freed} tokens freed]"
        )
    )

    result: list[BaseMessage] = []
    boundary_inserted = False
    for idx, msg in enumerate(messages):
        if idx in remove_set:
            if not boundary_inserted and idx == boundary_index:
                result.append(boundary)
                boundary_inserted = True
            continue
        result.append(msg)

    # 清理 AIMessage 中孤儿 tool_call 引用
    result = _cleanup_orphaned_tool_calls(result, removed_tool_ids)

    return result, CompactionStats(
        layer="snip",
        active=True,
        messages_before=len(messages),
        messages_after=len(result),
        tokens_freed=tokens_freed,
        details=f"{removed_count} old tool results removed",
    )
