from __future__ import annotations

from contextlib import contextmanager

import pytest
from langchain_core.messages import HumanMessage

from voice_code.agent import pipeline
from voice_code.telemetry.instrumentation import (
    MetricName,
    configure_telemetry_backend,
    normalize_metric_attributes,
    observe_event,
    record_counter,
    record_histogram,
    reset_telemetry_for_tests,
    start_span,
)
from voice_code.telemetry.otel import (
    OpenTelemetryBackend,
    configure_opentelemetry,
    duration_metric_views,
)


class CapturingBackend:
    def __init__(self) -> None:
        self.counters: list[tuple[str, float, dict[str, str]]] = []
        self.histograms: list[tuple[str, float, dict[str, str]]] = []
        self.spans: list[tuple[str, dict[str, str]]] = []

    def add_counter(self, name: str, value: float, attributes: dict[str, str]) -> None:
        self.counters.append((name, value, attributes))

    def record_histogram(self, name: str, value: float, attributes: dict[str, str]) -> None:
        self.histograms.append((name, value, attributes))

    @contextmanager
    def start_span(self, name: str, attributes: dict[str, str]):
        self.spans.append((name, attributes))
        yield


class FailingBackend(CapturingBackend):
    @contextmanager
    def start_span(self, name: str, attributes: dict[str, str]):
        raise RuntimeError("telemetry backend unavailable")
        yield


@pytest.fixture(autouse=True)
def _reset_backend() -> None:
    reset_telemetry_for_tests()
    yield
    reset_telemetry_for_tests()


def test_metric_schema_rejects_uncontracted_or_high_cardinality_labels() -> None:
    with pytest.raises(ValueError, match="session_id"):
        normalize_metric_attributes(
            MetricName.AGENT_TURNS_TOTAL,
            {"outcome": "success", "mode": "default", "session_id": "session-1"},
        )


def test_user_editable_metric_dimensions_fold_unknown_values_to_other() -> None:
    attributes = normalize_metric_attributes(
        MetricName.PERMISSION_DECISIONS_TOTAL,
        {
            "behavior": "deny",
            "risk_category": "tenant-specific-risk-name",
            "source": "workspace",
        },
    )

    assert attributes["risk_category"] == "other"


def test_metric_schema_normalizes_dynamic_values_and_caps_cardinality() -> None:
    first = normalize_metric_attributes(
        MetricName.LLM_REQUESTS_TOTAL,
        {"provider": "HTTP://provider.example/a", "model": "Model A", "outcome": "success"},
    )
    assert first == {
        "provider": "http_provider_example_a",
        "model": "model_a",
        "outcome": "success",
    }

    for index in range(1, 33):
        normalize_metric_attributes(
            MetricName.TOOL_CALLS_TOTAL,
            {"tool": f"user-defined-tool-{index}", "outcome": "success"},
        )
    overflow = normalize_metric_attributes(
        MetricName.TOOL_CALLS_TOTAL,
        {"tool": "one-more-user-tool", "outcome": "success"},
    )
    assert overflow["tool"] == "other"


def test_recording_uses_contract_attributes_and_seconds() -> None:
    backend = CapturingBackend()
    configure_telemetry_backend(backend)

    record_counter(
        MetricName.AGENT_TURNS_TOTAL,
        attributes={"outcome": "completed", "mode": "bypassPermissions"},
    )
    record_histogram(
        MetricName.AGENT_TURN_DURATION_SECONDS,
        1.25,
        attributes={"mode": "bypassPermissions"},
    )
    with start_span("agent.turn", {"outcome": "completed", "prompt": "must not escape"}):
        pass

    assert backend.counters == [
        ("agent_turns_total", 1.0, {"outcome": "completed", "mode": "bypasspermissions"})
    ]
    assert backend.histograms == [
        ("agent_turn_duration_seconds", 1.25, {"mode": "bypasspermissions"})
    ]
    assert backend.spans == [("agent.turn", {"outcome": "completed"})]


def test_default_backend_is_noop() -> None:
    record_counter(
        MetricName.SESSIONS_TOTAL,
        attributes={"operation": "load", "outcome": "success"},
    )
    with start_span("session.load", {"operation": "load"}):
        pass


def test_backend_failure_never_breaks_instrumented_product_code() -> None:
    configure_telemetry_backend(FailingBackend())
    executed = False

    with start_span("agent.turn"):
        executed = True

    assert executed is True


def test_opentelemetry_backend_exports_metrics_and_nested_spans() -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(
        metric_readers=[reader],
        shutdown_on_exit=False,
        views=duration_metric_views(),
    )
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(shutdown_on_exit=False)
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    configure_telemetry_backend(
        OpenTelemetryBackend(
            meter_provider.get_meter("voice-code"),
            tracer_provider.get_tracer("voice-code"),
        )
    )

    record_counter(
        MetricName.LLM_REQUESTS_TOTAL,
        attributes={"provider": "test", "model": "test", "outcome": "success"},
    )
    record_histogram(
        MetricName.LLM_REQUEST_DURATION_SECONDS,
        0.25,
        attributes={"provider": "test", "model": "test"},
    )
    with start_span("agent.turn"):
        with start_span("llm.request"):
            pass

    metric_names = {
        metric.name
        for resource_metric in reader.get_metrics_data().resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
    }
    assert metric_names == {"llm_requests_total", "llm_request_duration_seconds"}
    duration_metric = next(
        metric
        for resource_metric in reader.get_metrics_data().resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
        if metric.name == "llm_request_duration_seconds"
    )
    assert duration_metric.data.data_points[0].explicit_bounds == (
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1,
        2.5,
        5,
        10,
        30,
        60,
        120,
    )
    spans = span_exporter.get_finished_spans()
    assert [span.name for span in spans] == ["llm.request", "agent.turn"]
    assert spans[0].parent is not None


def test_opentelemetry_configuration_is_disabled_by_default() -> None:
    assert configure_opentelemetry(enabled=False) is False


def test_failed_operation_sets_opentelemetry_span_error_status() -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(shutdown_on_exit=False)
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    meter_provider = MeterProvider(shutdown_on_exit=False)
    configure_telemetry_backend(
        OpenTelemetryBackend(
            meter_provider.get_meter("voice-code"),
            tracer_provider.get_tracer("voice-code"),
        )
    )

    observe_event("llm.request.started", {"provider": "test", "model": "test"})
    observe_event(
        "llm.request.failed",
        {
            "provider": "test",
            "model": "test",
            "outcome": "timeout",
            "error_code": "PROVIDER_TIMEOUT",
        },
    )

    span = span_exporter.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error_code"] == "provider_timeout"


def test_fallback_and_permission_denial_have_bounded_error_metrics() -> None:
    backend = CapturingBackend()
    configure_telemetry_backend(backend)

    observe_event(
        "llm.request.started",
        {"provider": "Primary", "model": "Model A"},
    )
    observe_event(
        "llm.fallback.selected",
        {
            "provider": "Primary",
            "backend": "Fallback",
            "model": "Model A",
            "error_code": "PROVIDER_UNAVAILABLE",
            "duration_ms": 250,
        },
    )
    observe_event(
        "permission.decided",
        {
            "permission_behavior": "deny",
            "risk_category": "high",
            "rule_source": "workspace",
        },
    )

    assert backend.counters == [
        (
            "llm_fallback_total",
            1.0,
            {
                "from_provider": "primary",
                "reason": "provider_unavailable",
                "to_provider": "fallback",
            },
        ),
        (
            "llm_requests_total",
            1.0,
            {"model": "model_a", "outcome": "fallback", "provider": "primary"},
        ),
        (
            "permission_decisions_total",
            1.0,
            {"behavior": "deny", "risk_category": "high", "source": "workspace"},
        ),
    ]
    assert backend.histograms == [
        (
            "llm_request_duration_seconds",
            0.25,
            {"model": "model_a", "provider": "primary"},
        )
    ]


@pytest.mark.asyncio
async def test_auto_compaction_records_strategy_outcome_duration_and_span(monkeypatch) -> None:
    backend = CapturingBackend()
    configure_telemetry_backend(backend)

    monkeypatch.setattr(pipeline, "should_auto_compact", lambda _messages: True)

    async def fake_compact(messages, _model):
        return messages

    monkeypatch.setattr(pipeline, "compact_conversation", fake_compact)
    await pipeline.run_precompact_phase(
        [HumanMessage(content="private content")],
        model=object(),
        turn=1,
    )

    assert backend.counters == [
        ("compaction_total", 1.0, {"outcome": "success", "strategy": "auto"})
    ]
    assert backend.histograms[0][0] == "compaction_duration_seconds"
    assert backend.histograms[0][2] == {"strategy": "auto"}
    assert backend.spans == [("compact.run", {"strategy": "auto"})]
