"""Async-safe correlation context for logs and future metrics/traces."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True, slots=True)
class TelemetryContext:
    correlation_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    agent_id: str | None = None
    parent_agent_id: str | None = None
    task_id: str | None = None
    goal_id: str | None = None
    tool_call_id: str | None = None
    request_id: str | None = None
    memory_job_id: str | None = None
    memory_run_id: str | None = None

    def fields(self) -> dict[str, str]:
        return {key: value for key, value in asdict(self).items() if value is not None}


_TELEMETRY_CONTEXT: ContextVar[TelemetryContext] = ContextVar(
    "reasoning_telemetry_context",
    default=TelemetryContext(),
)


def current_telemetry_context() -> TelemetryContext:
    return _TELEMETRY_CONTEXT.get()


def new_telemetry_id(kind: str) -> str:
    prefix = "".join(char for char in kind.lower() if char.isalnum() or char == "_")
    return f"{prefix or 'id'}_{uuid.uuid4().hex}"


def new_correlation_id() -> str:
    return new_telemetry_id("corr")


@contextmanager
def bind_telemetry_context(**fields: str | None) -> Iterator[TelemetryContext]:
    """Merge fields into the current context and restore it on exit."""
    unknown = set(fields) - set(TelemetryContext.__dataclass_fields__)
    if unknown:
        raise ValueError(f"Unknown telemetry context fields: {', '.join(sorted(unknown))}")
    updates = {key: value for key, value in fields.items() if value is not None}
    context = replace(current_telemetry_context(), **updates)
    token = _TELEMETRY_CONTEXT.set(context)
    try:
        yield context
    finally:
        _TELEMETRY_CONTEXT.reset(token)
