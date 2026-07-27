"""Stable event names. Values are part of the public telemetry contract."""

from enum import StrEnum


class EventName(StrEnum):
    AGENT_TURN_STARTED = "agent.turn.started"
    AGENT_TURN_FINISHED = "agent.turn.finished"
    LLM_REQUEST_STARTED = "llm.request.started"
    LLM_REQUEST_FINISHED = "llm.request.finished"
    LLM_REQUEST_FAILED = "llm.request.failed"
    LLM_FALLBACK_SELECTED = "llm.fallback.selected"
    TOOL_EXECUTION_STARTED = "tool.execution.started"
    TOOL_EXECUTION_FINISHED = "tool.execution.finished"
    TOOL_EXECUTION_FAILED = "tool.execution.failed"
    PERMISSION_DECIDED = "permission.decided"
    SESSION_STATE_LOADED = "session.state.loaded"
    SESSION_STATE_SAVED = "session.state.saved"
    MEMORY_RETRIEVAL_DEGRADED = "memory.retrieval.degraded"
    MEMORY_EXTRACTION_ENQUEUE_FAILED = "memory.extraction.enqueue_failed"
