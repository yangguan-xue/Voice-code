"""ReactiveCompact — 遇到 context overflow 时压缩并重试."""

from __future__ import annotations

import logging

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

from reasoning_agent.compact.auto_compact import compact_conversation

logger = logging.getLogger(__name__)

_CONTEXT_OVERFLOW_PATTERNS = (
    "context length",
    "maximum context",
    "too long",
    "reduce the length",
    "token limit",
    "context window",
)


def is_context_overflow_error(error: Exception) -> bool:
    """判断异常是否为 context overflow（可恢复）。"""
    status_code = getattr(error, "status_code", None)
    if status_code == 413:
        return True

    body = str(error).lower()
    return any(pattern in body for pattern in _CONTEXT_OVERFLOW_PATTERNS)


async def try_reactive_compact(
    messages: list[BaseMessage],
    model: ChatOpenAI,
) -> list[BaseMessage]:
    """执行反应式压缩并返回压缩后的消息列表。"""
    logger.info("ReactiveCompact: compacting after context overflow")
    return await compact_conversation(messages, model)
