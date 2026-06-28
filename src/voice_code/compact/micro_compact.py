"""MicroCompact — 每轮清除旧的工具结果，零 API 开销"""

from __future__ import annotations

import logging

from langchain_core.messages import BaseMessage, ToolMessage

from voice_code.compact.token_count import rough_token_count_for_messages

logger = logging.getLogger(__name__)

_KEEP_RECENT = 12
_CLEARED_TEXT = "[Old tool output cleared]"

    # Tool result types eligible for clearing
_CLEARABLE_TOOLS = frozenset({
    "read", "glob", "grep", "bash",
    "edit", "write",
})


def should_clear_tool_result(tool_call_id: str) -> bool:
    """判断工具结果是否可清除（所有 Phase 1 工具都可清除）。"""
    return True  # Phase 1: all tools clearable


def apply_micro_compact(
    messages: list[BaseMessage],
    keep_recent: int = _KEEP_RECENT,
) -> tuple[list[BaseMessage], int]:
    """清除旧的工具结果，保留最近 keep_recent 个。

    Find all ToolMessages, keep last N, replace others with placeholder text.
    只处理最后一条用户消息之前的工具结果。
    """
    # 找到最后一条 HumanMessage 的位置
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].type == "human":
            last_user_idx = i
            break

    if last_user_idx <= 0:
        return messages, 0

    # 收集所有 ToolMessage 的位置（在最后一条用户消息之前）
    tool_indices: list[int] = []
    for i in range(last_user_idx):
        if isinstance(messages[i], ToolMessage):
            tool_indices.append(i)

    if len(tool_indices) <= keep_recent:
        return messages, 0

    tokens_freed = 0

    # 清除旧内容（保留最近 N 个）
    to_clear = tool_indices[: len(tool_indices) - keep_recent]
    for idx in to_clear:
        msg = messages[idx]
        if isinstance(msg, ToolMessage) and msg.content != _CLEARED_TEXT:
            before_tokens = max(1, rough_token_count_for_messages([msg]))
            messages[idx] = ToolMessage(
                content=_CLEARED_TEXT,
                tool_call_id=msg.tool_call_id,
            )
            tokens_freed += before_tokens

    logger.debug("MicroCompact: cleared %d old tool results", len(to_clear))
    return messages, tokens_freed
