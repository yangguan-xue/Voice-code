"""Vendor-neutral telemetry contracts shared by every application surface."""

from voice_code.telemetry.context import (
    TelemetryContext,
    bind_telemetry_context,
    current_telemetry_context,
    new_correlation_id,
    new_telemetry_id,
)
from voice_code.telemetry.errors import ErrorCode
from voice_code.telemetry.events import EventName
from voice_code.telemetry.instrumentation import (
    MetricName,
    record_counter,
    record_histogram,
    start_span,
)
from voice_code.telemetry.logging import configure_logging

__all__ = [
    "ErrorCode",
    "EventName",
    "MetricName",
    "TelemetryContext",
    "bind_telemetry_context",
    "configure_logging",
    "current_telemetry_context",
    "new_correlation_id",
    "new_telemetry_id",
    "record_counter",
    "record_histogram",
    "start_span",
]
