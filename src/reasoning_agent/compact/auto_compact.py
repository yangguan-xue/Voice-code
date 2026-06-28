"""AutoCompact — token 超限时调 LLM 生成对话摘要"""

from __future__ import annotations

import logging

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from reasoning_agent.compact.boundary import make_compact_boundary
from reasoning_agent.compact.prompt import format_compact_summary, get_compact_prompt
from reasoning_agent.compact.token_count import rough_token_count_for_messages

logger = logging.getLogger(__name__)

CONTEXT_WINDOW = 200_000
COMPACT_BUFFER = 20_000 + 13_000  # max_output + auto buffer
AUTO_COMPACT_THRESHOLD = CONTEXT_WINDOW - COMPACT_BUFFER  # 167_000
COMPACT_MAX_OUTPUT_TOKENS = 20_000
MAX_CONSECUTIVE_FAILURES = 3


def should_auto_compact(messages: list[BaseMessage]) -> bool:
    """检查是否应该触发自动压缩。"""
    tokens = rough_token_count_for_messages(messages)
    return tokens >= AUTO_COMPACT_THRESHOLD


def _messages_to_text(messages: list[BaseMessage]) -> str:
    """将消息列表转为纯文本（用于 compact prompt）。"""
    lines: list[str] = []
    for m in messages:
        role = getattr(m, "type", "unknown")
        content = getattr(m, "content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(
                p.get("text", "") if isinstance(p, dict) else str(p)
                for p in content
            )
        else:
            text = str(content)

        # 截断过长内容
        if len(text) > 2000:
            text = text[:2000] + "... (truncated)"

        lines.append(f"[{role}] {text}")
    return "\n\n".join(lines)


async def compact_conversation(
    messages: list[BaseMessage],
    model: ChatOpenAI,
    keep_recent: int = 10,
) -> list[BaseMessage]:
    """执行自动压缩：调 LLM 生成摘要，重建消息列表。

    Args:
        messages: 当前完整消息列表。
        model: LLM 客户端（用于生成摘要）。
        keep_recent: 保留最近 N 条消息不参与压缩。

    Returns:
        压缩后的消息列表: [boundary, summary, ...recent_messages]
    """
    pre_tokens = rough_token_count_for_messages(messages)

    # 分离"最近消息"（不压缩）和"旧消息"（被压缩）
    split_at = max(0, len(messages) - keep_recent)
    old_messages = messages[:split_at]
    recent_messages = messages[split_at:]

    if not old_messages:
        return messages

    # 构建 compact prompt：compact instruction + 旧消息文本
    compact_prompt = get_compact_prompt()
    old_text = _messages_to_text(old_messages)

    compact_messages: list = [
        SystemMessage(content=compact_prompt),
        HumanMessage(content=old_text),
    ]

    try:
        response = model.invoke(
            compact_messages,
            max_tokens=COMPACT_MAX_OUTPUT_TOKENS,
            temperature=0.0,
        )  # type: ignore[call-arg]
        summary = format_compact_summary(
            response.content if isinstance(response.content, str) else str(response.content)
        )
    except Exception:
        logger.exception("AutoCompact LLM call failed")
        return messages  # 压缩失败，保持原样

    # 构建压缩后的消息列表
    boundary = make_compact_boundary("auto", pre_tokens)
    summary_msg = HumanMessage(content=summary)

    result: list[BaseMessage] = [boundary, summary_msg]
    result.extend(recent_messages)

    post_tokens = rough_token_count_for_messages(result)
    logger.info(
        "AutoCompact: %d tokens → %d tokens, %d messages → %d messages",
        pre_tokens, post_tokens,
        len(messages), len(result),
    )

    return result
