"""fork 子 agent 辅助。"""

from __future__ import annotations

_FORK_TAG = "<fork_subagent>"


def build_fork_system_prompt(parent_system_prompt: str) -> str:
    return (
        f"{parent_system_prompt}\n\n"
        f"{_FORK_TAG}\n"
        "你是 fork 出来的 worker。不要再继续派生子 agent。"
        " 直接使用工具完成当前范围内的工作，最后一次性汇报。\n"
        f"{_FORK_TAG}"
    )


def validate_fork_prompt(prompt: str) -> None:
    if _FORK_TAG in prompt:
        raise ValueError("Fork prompt recursion detected")
