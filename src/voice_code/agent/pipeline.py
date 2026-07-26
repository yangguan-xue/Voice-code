"""Composable runtime phases extracted from the agent loop coordinator."""

from __future__ import annotations

import time
from dataclasses import dataclass

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

from voice_code.agent.types import AgentEvent, EventType
from voice_code.compact import (
    apply_micro_compact,
    collapse_old_turns,
    compact_conversation,
    should_auto_compact,
    snip_compact,
)
from voice_code.telemetry import MetricName, record_counter, record_histogram, start_span


@dataclass(slots=True)
class RuntimePhaseResult:
    messages: list[BaseMessage]
    events: list[AgentEvent]


async def run_precompact_phase(
    messages: list[BaseMessage],
    *,
    model: ChatOpenAI,
    turn: int,
) -> RuntimePhaseResult:
    """Run deterministic and model-backed pre-compaction as one phase."""
    events: list[AgentEvent] = []
    stats_parts: list[str] = []
    messages, micro_tokens_freed = apply_micro_compact(messages)
    if micro_tokens_freed > 0:
        stats_parts.append(f"micro ~{micro_tokens_freed} tok")

    messages, snip_stats = snip_compact(messages)
    if snip_stats.active:
        stats_parts.append(f"snip {snip_stats.details} ~{snip_stats.tokens_freed} tok")

    messages, collapse_stats = collapse_old_turns(messages)
    if collapse_stats.active:
        stats_parts.append(f"collapse {collapse_stats.details}")

    if stats_parts:
        events.append(
            AgentEvent(
                type=EventType.ERROR,
                turn=turn,
                content="compact: " + " | ".join(stats_parts),
                phase="compacting",
                status="compact",
            )
        )

    if should_auto_compact(messages):
        started_at = time.monotonic()
        outcome = "error"
        try:
            with start_span("compact.run", {"strategy": "auto"}):
                messages = await compact_conversation(messages, model)
            outcome = "success"
        finally:
            record_counter(
                MetricName.COMPACTION_TOTAL,
                attributes={"strategy": "auto", "outcome": outcome},
            )
            record_histogram(
                MetricName.COMPACTION_DURATION_SECONDS,
                time.monotonic() - started_at,
                attributes={"strategy": "auto"},
            )
        events.append(
            AgentEvent(
                type=EventType.ERROR,
                turn=turn,
                content="Conversation compacted (auto)",
                phase="compacting",
                status="compact",
            )
        )
    return RuntimePhaseResult(messages=messages, events=events)
