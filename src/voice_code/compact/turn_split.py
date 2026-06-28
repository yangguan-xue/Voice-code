"""Turn splitting helpers shared by compact strategies."""

from __future__ import annotations

from langchain_core.messages import BaseMessage


def split_into_turns(
    messages: list[BaseMessage],
) -> tuple[list[BaseMessage], list[list[BaseMessage]]]:
    """Split messages into prefix + human-bounded turns."""
    prefix: list[BaseMessage] = []
    start_idx = 0
    for idx, msg in enumerate(messages):
        if getattr(msg, "type", "") == "human":
            start_idx = idx
            break
        prefix.append(msg)
        start_idx = idx + 1

    if start_idx >= len(messages):
        return messages, []

    turns: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []
    for msg in messages[start_idx:]:
        if getattr(msg, "type", "") == "human":
            if current:
                turns.append(current)
            current = [msg]
        else:
            current.append(msg)

    if current:
        turns.append(current)

    return prefix, turns
