"""Compact module — context compression for agent loop"""

from __future__ import annotations

from voice_code.compact.auto_compact import (
    AUTO_COMPACT_THRESHOLD,
    compact_conversation,
    should_auto_compact,
)
from voice_code.compact.context_collapse import collapse_old_turns
from voice_code.compact.micro_compact import apply_micro_compact
from voice_code.compact.reactive_compact import (
    is_context_overflow_error,
    try_reactive_compact,
)
from voice_code.compact.snip import snip_compact
from voice_code.compact.stats import CompactionStats
from voice_code.compact.token_count import rough_token_count_for_messages

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
