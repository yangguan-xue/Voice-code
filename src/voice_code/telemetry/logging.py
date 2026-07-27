"""Structured and readable logging backed by one privacy-safe contract."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import IO, Any

from voice_code.security import redact_secrets
from voice_code.telemetry.context import current_telemetry_context
from voice_code.telemetry.instrumentation import observe_event
from voice_code.telemetry.otel import configure_opentelemetry

logger = logging.getLogger(__name__)

_CONTEXT_FIELDS = frozenset(current_telemetry_context().fields()) | frozenset(
    current_telemetry_context().__dataclass_fields__
)
_CONTENT_FIELDS = frozenset(
    {
        "authorization",
        "cookie",
        "evidence",
        "memory_content",
        "prompt",
        "response_content",
        "tool_args",
        "tool_output",
        "transcript",
        "tts_text",
        "user_input",
        "voice_text",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "attempt",
        "backend",
        "duration_ms",
        "environment",
        "error_code",
        "event",
        "exception_type",
        "mode",
        "model",
        "operation",
        "outcome",
        "permission_behavior",
        "provider",
        "risk_category",
        "rule_source",
        "service_name",
        "service_version",
        "stage",
        "tool_name",
    }
)
_ALLOWED_EXTRA_FIELDS = _CONTEXT_FIELDS | _CONTENT_FIELDS | _EVENT_FIELDS


def _service_version() -> str:
    try:
        return version("voice-code")
    except PackageNotFoundError:
        return "unknown"


def _text(value: object) -> str:
    return str(redact_secrets(str(value)))


def _build_payload(record: logging.LogRecord) -> dict[str, Any]:
    timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds")
    payload: dict[str, Any] = {
        "timestamp": timestamp.replace("+00:00", "Z"),
        "level": record.levelname.lower(),
        "service_name": _text(getattr(record, "service_name", "voice-code")),
        "service_version": _text(getattr(record, "service_version", _service_version())),
        "environment": _text(
            getattr(record, "environment", os.environ.get("REASONING_ENVIRONMENT", "local"))
        ),
    }
    context_fields = current_telemetry_context().fields()
    for key in _ALLOWED_EXTRA_FIELDS:
        if key in _CONTENT_FIELDS and hasattr(record, key):
            payload[key] = "[REDACTED]"
            continue
        if hasattr(record, key):
            value = getattr(record, key)
        else:
            value = context_fields.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (str, int, float, bool)):
            payload[key] = redact_secrets(value)
        else:
            payload[key] = _text(value)

    event = payload.get("event")
    payload["event"] = _text(event) if event else "log.message"
    payload["message"] = _text(record.getMessage())
    if record.exc_info and record.exc_info[0] is not None:
        payload["exception_type"] = record.exc_info[0].__name__
    return payload


class JsonTelemetryFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_build_payload(record), ensure_ascii=False, separators=(",", ":"))


class ReadableTelemetryFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = _build_payload(record)
        context = payload.get("correlation_id", "-")
        error = f" error_code={payload['error_code']}" if payload.get("error_code") else ""
        exception = (
            f" exception_type={payload['exception_type']}"
            if payload.get("exception_type")
            else ""
        )
        return (
            f"{payload['level'].upper()} [{payload['event']}] "
            f"correlation_id={context}{error}{exception} {payload['message']}"
        )


class TelemetryEventFilter(logging.Filter):
    """Keep production INFO focused on contract events, while preserving failures."""

    def __init__(self, *, debug: bool) -> None:
        super().__init__()
        self._debug = debug

    def filter(self, record: logging.LogRecord) -> bool:
        allowed = self._debug or record.levelno >= logging.WARNING or hasattr(record, "event")
        if allowed and hasattr(record, "event") and not hasattr(record, "_telemetry_observed"):
            record._telemetry_observed = True
            fields = {
                key: getattr(record, key)
                for key in _EVENT_FIELDS
                if hasattr(record, key)
            }
            try:
                observe_event(str(record.event), fields)
            except Exception:
                pass
        return allowed


def configure_logging(
    *,
    debug: bool = False,
    json_output: bool | None = None,
    stream: IO[str] | None = None,
    force: bool = True,
) -> None:
    """Configure every product entry point with the same logging contract."""
    if json_output is None:
        json_output = os.environ.get("REASONING_LOG_FORMAT", "console").lower() == "json"
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonTelemetryFormatter() if json_output else ReadableTelemetryFormatter())
    handler.addFilter(TelemetryEventFilter(debug=debug))

    root = logging.getLogger()
    if force:
        for existing in root.handlers[:]:
            root.removeHandler(existing)
            existing.close()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        configure_opentelemetry()
    except Exception:
        logger.warning("OpenTelemetry disabled after configuration failure")
