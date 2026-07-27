from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from voice_code import __version__
from voice_code.telemetry import current_telemetry_context

USER_AGENT = f"voice-code-audit/{__version__}"

_AUDIT_FIELDS = {
    "event_type",
    "actor",
    "timestamp",
    "resource_id",
    "outcome",
    "correlation_id",
    "session_id",
    "turn_id",
    "agent_id",
    "rule",
    "approval_result",
}
_audit_sink: ContextVar[list[dict[str, str]] | None] = ContextVar("audit_sink", default=None)


class AuditRecorder:
    def __init__(self, sink: list[dict[str, str]] | None = None) -> None:
        self._sink = sink

    def record(
        self,
        *,
        event_type: str,
        actor: str,
        resource_id: str,
        outcome: str,
        rule: str | None = None,
        approval_result: str | None = None,
        **_: Any,
    ) -> dict[str, str]:
        context = current_telemetry_context()
        event = {
            "event_type": event_type,
            "actor": actor,
            "timestamp": datetime.now(UTC).isoformat(),
            "resource_id": resource_id,
            "outcome": outcome,
        }
        optional = {
            "correlation_id": context.correlation_id or "",
            "session_id": context.session_id or "",
            "turn_id": context.turn_id or "",
            "agent_id": context.agent_id or "",
            "rule": rule,
            "approval_result": approval_result,
        }
        event.update({key: value for key, value in optional.items() if value is not None})
        event = {key: value for key, value in event.items() if key in _AUDIT_FIELDS}
        sink = self._sink if self._sink is not None else _audit_sink.get()
        if sink is not None:
            sink.append(event)
        return event


def record_audit_event(
    *,
    event_type: str,
    actor: str,
    resource_id: str,
    outcome: str,
    rule: str | None = None,
    approval_result: str | None = None,
    **fields: Any,
) -> dict[str, str]:
    return AuditRecorder().record(
        event_type=event_type,
        actor=actor,
        resource_id=resource_id,
        outcome=outcome,
        rule=rule,
        approval_result=approval_result,
        **fields,
    )


@contextmanager
def capture_audit_events() -> Iterator[list[dict[str, str]]]:
    events: list[dict[str, str]] = []
    token = _audit_sink.set(events)
    try:
        yield events
    finally:
        _audit_sink.reset(token)
