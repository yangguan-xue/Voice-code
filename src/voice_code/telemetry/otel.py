"""Optional OpenTelemetry SDK bridge configured through OTLP environment variables."""

from __future__ import annotations

import atexit
import logging
import os
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from threading import Lock
from typing import Any

from voice_code.telemetry.instrumentation import configure_telemetry_backend

logger = logging.getLogger(__name__)

_configured = False
_providers: tuple[Any, Any] | None = None
_DURATION_BOUNDARIES = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)
_DURATION_METRICS = (
    "agent_turn_duration_seconds",
    "llm_request_duration_seconds",
    "compaction_duration_seconds",
    "tool_duration_seconds",
    "session_resume_duration_seconds",
)


class _OpenTelemetrySpan:
    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, name: str, value: str) -> None:
        self._span.set_attribute(name, value)

    def mark_error(self) -> None:
        from opentelemetry.trace import Status, StatusCode

        self._span.set_status(Status(StatusCode.ERROR))


class OpenTelemetryBackend:
    """Translate the local contract to OpenTelemetry API instruments."""

    def __init__(self, meter: Any, tracer: Any) -> None:
        self._meter = meter
        self._tracer = tracer
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._lock = Lock()

    def _counter(self, name: str) -> Any:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = self._meter.create_counter(
                    name,
                    unit="1",
                    description=f"Voice Code {name.replace('_', ' ')}",
                )
            return self._counters[name]

    def _histogram(self, name: str) -> Any:
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = self._meter.create_histogram(
                    name,
                    unit="s" if name.endswith("_seconds") else "1",
                    description=f"Voice Code {name.replace('_', ' ')}",
                )
            return self._histograms[name]

    def add_counter(self, name: str, value: float, attributes: dict[str, str]) -> None:
        self._counter(name).add(value, attributes)

    def record_histogram(
        self, name: str, value: float, attributes: dict[str, str]
    ) -> None:
        self._histogram(name).record(value, attributes)

    @contextmanager
    def start_span(self, name: str, attributes: dict[str, str]):
        with self._tracer.start_as_current_span(
            name,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            yield _OpenTelemetrySpan(span)


def _service_version() -> str:
    try:
        return version("voice-code")
    except PackageNotFoundError:
        return "unknown"


def _enabled_from_environment() -> bool:
    return os.environ.get("REASONING_OTEL_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def duration_metric_views() -> list[Any]:
    """Use seconds-scale buckets instead of OTel's millisecond-oriented defaults."""
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View

    return [
        View(
            instrument_name=name,
            aggregation=ExplicitBucketHistogramAggregation(boundaries=_DURATION_BOUNDARIES),
        )
        for name in _DURATION_METRICS
    ]


def _shutdown() -> None:
    global _providers
    if _providers is None:
        return
    meter_provider, tracer_provider = _providers
    meter_provider.shutdown()
    tracer_provider.shutdown()
    _providers = None


def configure_opentelemetry(*, enabled: bool | None = None) -> bool:
    """Enable OTLP metrics/traces when explicitly requested and installed."""
    global _configured, _providers
    should_enable = _enabled_from_environment() if enabled is None else enabled
    if not should_enable:
        return False
    if _configured:
        return True

    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "OpenTelemetry requested but optional dependencies are not installed; "
            "install voice-code[telemetry]"
        )
        return False

    resource = Resource.create(
        {
            "service.name": "voice-code",
            "service.version": _service_version(),
            "deployment.environment.name": os.environ.get(
                "REASONING_ENVIRONMENT", "local"
            ),
        }
    )
    metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    meter_provider = MeterProvider(
        metric_readers=[metric_reader],
        resource=resource,
        shutdown_on_exit=False,
        views=duration_metric_views(),
    )
    tracer_provider = TracerProvider(resource=resource, shutdown_on_exit=False)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    configure_telemetry_backend(
        OpenTelemetryBackend(
            meter_provider.get_meter("voice-code", _service_version()),
            tracer_provider.get_tracer("voice-code", _service_version()),
        )
    )
    _providers = (meter_provider, tracer_provider)
    _configured = True
    atexit.register(_shutdown)
    return True
