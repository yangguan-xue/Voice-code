"""Vendor-neutral metrics and tracing with bounded telemetry attributes."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Protocol

from voice_code.telemetry.context import current_telemetry_context


class MetricName(StrEnum):
    AGENT_TURNS_TOTAL = "agent_turns_total"
    AGENT_TURN_DURATION_SECONDS = "agent_turn_duration_seconds"
    LLM_REQUESTS_TOTAL = "llm_requests_total"
    LLM_REQUEST_DURATION_SECONDS = "llm_request_duration_seconds"
    LLM_TOKENS_TOTAL = "llm_tokens_total"
    LLM_FALLBACK_TOTAL = "llm_fallback_total"
    COMPACTION_TOTAL = "compaction_total"
    COMPACTION_DURATION_SECONDS = "compaction_duration_seconds"
    TOOL_CALLS_TOTAL = "tool_calls_total"
    TOOL_DURATION_SECONDS = "tool_duration_seconds"
    PERMISSION_DECISIONS_TOTAL = "permission_decisions_total"
    SESSIONS_TOTAL = "sessions_total"
    SESSION_RESUME_DURATION_SECONDS = "session_resume_duration_seconds"
    VOICE_STAGE_TOTAL = "voice_stage_total"
    VOICE_STAGE_DURATION_SECONDS = "voice_stage_duration_seconds"
    SUBAGENT_TASKS_TOTAL = "subagent_tasks_total"
    SUBAGENT_TASK_DURATION_SECONDS = "subagent_task_duration_seconds"
    GOAL_ITERATIONS_TOTAL = "goal_iterations_total"
    GOAL_ITERATION_DURATION_SECONDS = "goal_iteration_duration_seconds"
    RAG_RETRIEVALS_TOTAL = "rag_retrievals_total"
    RAG_RETRIEVAL_DURATION_SECONDS = "rag_retrieval_duration_seconds"
    RAG_EXTRACTIONS_TOTAL = "rag_extractions_total"
    RAG_EXTRACTION_DURATION_SECONDS = "rag_extraction_duration_seconds"
    RAG_OUTBOX_TOTAL = "rag_outbox_total"
    RAG_OUTBOX_DURATION_SECONDS = "rag_outbox_duration_seconds"


_METRIC_LABELS: dict[MetricName, frozenset[str]] = {
    MetricName.AGENT_TURNS_TOTAL: frozenset({"outcome", "mode"}),
    MetricName.AGENT_TURN_DURATION_SECONDS: frozenset({"mode"}),
    MetricName.LLM_REQUESTS_TOTAL: frozenset({"provider", "model", "outcome"}),
    MetricName.LLM_REQUEST_DURATION_SECONDS: frozenset({"provider", "model"}),
    MetricName.LLM_TOKENS_TOTAL: frozenset({"provider", "model", "direction"}),
    MetricName.LLM_FALLBACK_TOTAL: frozenset({"from_provider", "to_provider", "reason"}),
    MetricName.COMPACTION_TOTAL: frozenset({"strategy", "outcome"}),
    MetricName.COMPACTION_DURATION_SECONDS: frozenset({"strategy"}),
    MetricName.TOOL_CALLS_TOTAL: frozenset({"tool", "outcome"}),
    MetricName.TOOL_DURATION_SECONDS: frozenset({"tool"}),
    MetricName.PERMISSION_DECISIONS_TOTAL: frozenset(
        {"behavior", "risk_category", "source"}
    ),
    MetricName.SESSIONS_TOTAL: frozenset({"operation", "outcome"}),
    MetricName.SESSION_RESUME_DURATION_SECONDS: frozenset(),
    MetricName.VOICE_STAGE_TOTAL: frozenset({"stage", "outcome"}),
    MetricName.VOICE_STAGE_DURATION_SECONDS: frozenset({"stage"}),
    MetricName.SUBAGENT_TASKS_TOTAL: frozenset({"agent_type", "outcome"}),
    MetricName.SUBAGENT_TASK_DURATION_SECONDS: frozenset({"agent_type"}),
    MetricName.GOAL_ITERATIONS_TOTAL: frozenset({"stage", "outcome"}),
    MetricName.GOAL_ITERATION_DURATION_SECONDS: frozenset({"stage"}),
    MetricName.RAG_RETRIEVALS_TOTAL: frozenset({"backend", "outcome"}),
    MetricName.RAG_RETRIEVAL_DURATION_SECONDS: frozenset({"backend"}),
    MetricName.RAG_EXTRACTIONS_TOTAL: frozenset({"mode", "outcome"}),
    MetricName.RAG_EXTRACTION_DURATION_SECONDS: frozenset({"mode"}),
    MetricName.RAG_OUTBOX_TOTAL: frozenset({"backend", "outcome"}),
    MetricName.RAG_OUTBOX_DURATION_SECONDS: frozenset({"backend"}),
}
_DYNAMIC_LABELS = frozenset(
    {"provider", "from_provider", "to_provider", "model", "tool", "agent_type"}
)
_ENUM_VALUES: dict[str, frozenset[str]] = {
    "behavior": frozenset({"allow", "ask", "deny", "unknown"}),
    "direction": frozenset({"input", "output"}),
    "mode": frozenset(
        {
            "default",
            "acceptedits",
            "bypasspermissions",
            "dontask",
            "automatic",
            "shadow",
            "manual",
            "off",
            "unknown",
        }
    ),
    "operation": frozenset({"load", "save", "resume", "unknown"}),
    "risk_category": frozenset({"high", "medium", "low", "unknown"}),
    "source": frozenset({"built_in", "workspace", "session", "runtime", "none", "unknown"}),
    "stage": frozenset(
        {
            "vad",
            "stt",
            "agent",
            "tts",
            "playback",
            "build",
            "verify",
            "review",
            "rollback",
            "budget",
            "queue",
            "outbox",
            "unknown",
        }
    ),
    "backend": frozenset({"milvus", "sqlite_fts", "none", "unknown"}),
    "strategy": frozenset({"auto", "reactive", "micro", "snip", "collapse", "unknown"}),
}
_MAX_DYNAMIC_VALUES = 32
_dynamic_values: dict[str, set[str]] = {}
_SAFE_VALUE = re.compile(r"[^a-z0-9_-]+")
_TRACE_FIELDS = frozenset(
    {
        "agent_type",
        "attempt",
        "backend",
        "behavior",
        "direction",
        "error_code",
        "mode",
        "model",
        "operation",
        "outcome",
        "provider",
        "reason",
        "risk_category",
        "source",
        "stage",
        "strategy",
        "tool",
    }
)
_ERROR_OUTCOMES = frozenset(
    {"cancelled", "context_overflow", "error", "fallback", "invalid", "not_found", "timeout"}
)


class TelemetryBackend(Protocol):
    def add_counter(self, name: str, value: float, attributes: dict[str, str]) -> None: ...

    def record_histogram(
        self, name: str, value: float, attributes: dict[str, str]
    ) -> None: ...

    def start_span(self, name: str, attributes: dict[str, str]): ...


class _NoopBackend:
    def add_counter(self, name: str, value: float, attributes: dict[str, str]) -> None:
        return None

    def record_histogram(
        self, name: str, value: float, attributes: dict[str, str]
    ) -> None:
        return None

    @contextmanager
    def start_span(self, name: str, attributes: dict[str, str]) -> Iterator[None]:
        yield None


_backend: TelemetryBackend = _NoopBackend()
_active_scopes: ContextVar[dict[str, tuple[object, object | None]]] = ContextVar(
    "reasoning_telemetry_spans",
    default={},
)


def _normalize_value(value: object) -> str:
    normalized = _SAFE_VALUE.sub("_", str(value).strip().lower()).strip("_.-")
    return normalized[:64] or "unknown"


def _bounded_value(key: str, value: object) -> str:
    normalized = _normalize_value(value)
    if key in _ENUM_VALUES:
        return normalized if normalized in _ENUM_VALUES[key] else "other"
    if key not in _DYNAMIC_LABELS:
        return normalized
    seen = _dynamic_values.setdefault(key, set())
    if normalized in seen:
        return normalized
    if len(seen) >= _MAX_DYNAMIC_VALUES:
        return "other"
    seen.add(normalized)
    return normalized


def normalize_metric_attributes(
    name: MetricName, attributes: Mapping[str, object] | None = None
) -> dict[str, str]:
    """Validate label keys and bound attacker- or config-controlled values."""
    supplied = dict(attributes or {})
    allowed = _METRIC_LABELS[name]
    unknown = set(supplied) - allowed
    if unknown:
        raise ValueError(f"Metric {name} does not allow labels: {', '.join(sorted(unknown))}")
    return {key: _bounded_value(key, supplied.get(key, "unknown")) for key in sorted(allowed)}


def normalize_trace_attributes(attributes: Mapping[str, object] | None = None) -> dict[str, str]:
    supplied = dict(attributes or {})
    safe = {
        key: _normalize_value(value)
        for key, value in supplied.items()
        if key in _TRACE_FIELDS and value not in (None, "")
    }
    safe.update(current_telemetry_context().fields())
    return safe


def configure_telemetry_backend(backend: TelemetryBackend) -> None:
    global _backend
    _backend = backend


def reset_telemetry_for_tests() -> None:
    global _backend
    _backend = _NoopBackend()
    _dynamic_values.clear()
    _active_scopes.set({})


def record_counter(
    name: MetricName,
    value: float = 1.0,
    *,
    attributes: Mapping[str, object] | None = None,
) -> None:
    if isinstance(_backend, _NoopBackend):
        return None
    try:
        _backend.add_counter(name.value, value, normalize_metric_attributes(name, attributes))
    except Exception:
        return None


def record_histogram(
    name: MetricName,
    value: float,
    *,
    attributes: Mapping[str, object] | None = None,
) -> None:
    if isinstance(_backend, _NoopBackend):
        return None
    try:
        _backend.record_histogram(
            name.value,
            value,
            normalize_metric_attributes(name, attributes),
        )
    except Exception:
        return None


@contextmanager
def start_span(
    name: str, attributes: Mapping[str, object] | None = None
) -> Iterator[object | None]:
    if isinstance(_backend, _NoopBackend):
        yield None
        return
    try:
        scope = _backend.start_span(name, normalize_trace_attributes(attributes))
        span = scope.__enter__()
    except Exception:
        yield None
        return
    try:
        try:
            yield span
        except Exception:
            if span is not None and hasattr(span, "mark_error"):
                span.mark_error()
            raise
    finally:
        try:
            scope.__exit__(None, None, None)
        except Exception:
            pass


def _duration_seconds(fields: Mapping[str, object]) -> float:
    try:
        return max(0.0, float(fields.get("duration_ms", 0.0))) / 1000
    except (TypeError, ValueError):
        return 0.0


def _open_span(key: str, name: str, fields: Mapping[str, object]) -> None:
    scopes = dict(_active_scopes.get())
    if key in scopes:
        _close_span(key, {"outcome": "superseded"})
        scopes = dict(_active_scopes.get())
    scope = start_span(name, fields)
    span = scope.__enter__()
    scopes[key] = (scope, span)
    _active_scopes.set(scopes)


def _close_span(key: str, fields: Mapping[str, object]) -> bool:
    scopes = dict(_active_scopes.get())
    item = scopes.pop(key, None)
    _active_scopes.set(scopes)
    if item is None:
        return False
    scope, span = item
    try:
        if span is not None and hasattr(span, "set_attribute"):
            for name, value in normalize_trace_attributes(fields).items():
                span.set_attribute(name, value)
        outcome = _normalize_value(fields.get("outcome", ""))
        if (
            span is not None
            and hasattr(span, "mark_error")
            and (fields.get("error_code") or outcome in _ERROR_OUTCOMES)
        ):
            span.mark_error()
    finally:
        scope.__exit__(None, None, None)
    return True


def observe_event(event: str, fields: Mapping[str, object]) -> None:
    """Project stable log events into metrics and operation spans."""
    if isinstance(_backend, _NoopBackend):
        return
    if event == "agent.turn.started":
        _open_span("agent.turn", "agent.turn", fields)
        return
    if event == "agent.turn.finished":
        labels = {"outcome": fields.get("outcome"), "mode": fields.get("mode")}
        record_counter(MetricName.AGENT_TURNS_TOTAL, attributes=labels)
        record_histogram(
            MetricName.AGENT_TURN_DURATION_SECONDS,
            _duration_seconds(fields),
            attributes={"mode": fields.get("mode")},
        )
        _close_span("agent.turn", fields)
        return
    if event == "llm.request.started":
        _open_span("llm.request", "llm.request", fields)
        return
    if event in {"llm.request.finished", "llm.request.failed"}:
        if "llm.request" not in _active_scopes.get():
            return
        labels = {
            "provider": fields.get("provider"),
            "model": fields.get("model"),
            "outcome": fields.get("outcome"),
        }
        record_counter(MetricName.LLM_REQUESTS_TOTAL, attributes=labels)
        record_histogram(
            MetricName.LLM_REQUEST_DURATION_SECONDS,
            _duration_seconds(fields),
            attributes={"provider": fields.get("provider"), "model": fields.get("model")},
        )
        _close_span("llm.request", fields)
        return
    if event == "llm.fallback.selected":
        record_counter(
            MetricName.LLM_FALLBACK_TOTAL,
            attributes={
                "from_provider": fields.get("provider"),
                "to_provider": fields.get("backend"),
                "reason": fields.get("error_code"),
            },
        )
        record_counter(
            MetricName.LLM_REQUESTS_TOTAL,
            attributes={
                "provider": fields.get("provider"),
                "model": fields.get("model"),
                "outcome": "fallback",
            },
        )
        record_histogram(
            MetricName.LLM_REQUEST_DURATION_SECONDS,
            _duration_seconds(fields),
            attributes={"provider": fields.get("provider"), "model": fields.get("model")},
        )
        _close_span("llm.request", fields)
        return
    if event == "tool.execution.started":
        _open_span("tool.execute", "tool.execute", fields)
        return
    if event in {"tool.execution.finished", "tool.execution.failed"}:
        tool = fields.get("tool_name") or fields.get("tool")
        record_counter(
            MetricName.TOOL_CALLS_TOTAL,
            attributes={"tool": tool, "outcome": fields.get("outcome")},
        )
        record_histogram(
            MetricName.TOOL_DURATION_SECONDS,
            _duration_seconds(fields),
            attributes={"tool": tool},
        )
        _close_span("tool.execute", fields)
        return
    if event == "permission.decided":
        record_counter(
            MetricName.PERMISSION_DECISIONS_TOTAL,
            attributes={
                "behavior": fields.get("permission_behavior"),
                "risk_category": fields.get("risk_category"),
                "source": fields.get("rule_source"),
            },
        )
        with start_span("permission.evaluate", fields):
            pass
        return
    if event in {"session.state.loaded", "session.state.saved"}:
        record_counter(
            MetricName.SESSIONS_TOTAL,
            attributes={"operation": fields.get("operation"), "outcome": fields.get("outcome")},
        )
        with start_span(f"session.{fields.get('operation', 'state')}", fields) as span:
            if span is not None and hasattr(span, "mark_error") and fields.get("error_code"):
                span.mark_error()
