"""Compact module — context compression for agent loop"""

from __future__ import annotations

from reasoning_agent.compact.auto_compact import (
    AUTO_COMPACT_THRESHOLD,
    compact_conversation,
    should_auto_compact,
)
from reasoning_agent.compact.context_collapse import collapse_old_turns
from reasoning_agent.compact.micro_compact import apply_micro_compact
from reasoning_agent.compact.reactive_compact import (
    is_context_overflow_error,
    try_reactive_compact,
)
from reasoning_agent.compact.snip import snip_compact
from reasoning_agent.compact.stats import CompactionStats
from reasoning_agent.compact.token_count import rough_token_count_for_messages

__all__ = [
    "apply_micro_compact",
    "snip_compact",
    "collapse_old_turns",
    "CompactionStats",
    "should_auto_compact",
    "compact_conversation",
    "is_context_overflow_error",
    "try_reactive_compact",
    "rough_token_count_for_messages",
    "AUTO_COMPACT_THRESHOLD",
]
