"""Compaction stats dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompactionStats:
    """Single compaction layer stats."""

    layer: str
    active: bool = False
    messages_before: int = 0
    messages_after: int = 0
    tokens_freed: int = 0
    details: str = ""
