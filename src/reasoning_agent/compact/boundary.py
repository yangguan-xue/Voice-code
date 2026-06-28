"""压缩边界标记 — cc-haha 的 compact boundary message"""

from __future__ import annotations

from langchain_core.messages import SystemMessage


def make_compact_boundary(trigger: str, pre_tokens: int) -> SystemMessage:
    """创建压缩边界系统消息。

    Args:
        trigger: 'auto' 或 'manual'
        pre_tokens: 压缩前的 token 估算数
    """
    content = (
        f"Conversation compacted ({trigger}). "
        f"Pre-compact token count: ~{pre_tokens}. "
        "Messages before this point have been summarized."
    )
    return SystemMessage(content=content)


def get_messages_after_boundary(messages: list) -> list:
    """获取压缩边界之后的消息。

    从后往前扫描，找到第一个 compact boundary 消息，
    返回它及其之后的所有消息。
    """
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        content = getattr(m, "content", "")
        if isinstance(content, str) and "Conversation compacted" in content:
            return messages[i:]
    return messages
