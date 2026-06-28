"""Token 估算 — rough estimate: content length / 4"""

from __future__ import annotations

from langchain_core.messages import BaseMessage

_CHARS_PER_TOKEN = 4


def rough_token_count(text: str) -> int:
    """粗略 token 估算：字符数 / 4。"""
    return len(text) // _CHARS_PER_TOKEN


def rough_token_count_for_messages(messages: list[BaseMessage]) -> int:
    """估算消息列表的总 token 数。"""
    total = 0
    for m in messages:
        parts = getattr(m, "content", None)
        if isinstance(parts, str):
            total += rough_token_count(parts)
        elif isinstance(parts, list):
            for p in parts:
                if isinstance(p, dict):
                    text = p.get("text", "") or str(p)
                    total += rough_token_count(str(text))
                elif isinstance(p, str):
                    total += rough_token_count(p)
        # tool_calls
        for tc in getattr(m, "tool_calls", []) or []:
            if isinstance(tc, dict):
                total += rough_token_count(str(tc.get("name", "")))
                total += rough_token_count(str(tc.get("args", {})))
            else:
                total += rough_token_count(str(tc))
    return total
