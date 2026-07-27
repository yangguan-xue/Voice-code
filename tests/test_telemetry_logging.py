from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest

from voice_code.telemetry.context import (
    TelemetryContext,
    bind_telemetry_context,
    current_telemetry_context,
    new_correlation_id,
)
from voice_code.telemetry.errors import ErrorCode
from voice_code.telemetry.events import EventName
from voice_code.telemetry.logging import configure_logging


def _emit_json_log(**extra: object) -> dict[str, object]:
    stream = io.StringIO()
    configure_logging(json_output=True, stream=stream, force=True)
    logging.getLogger("test.telemetry").info("agent turn started", extra=extra)
    return json.loads(stream.getvalue())


def test_context_is_nested_and_restored() -> None:
    assert current_telemetry_context() == TelemetryContext()

    with bind_telemetry_context(session_id="session-1", correlation_id="corr-1"):
        assert current_telemetry_context().session_id == "session-1"
        with bind_telemetry_context(turn_id="turn-1"):
            nested = current_telemetry_context()
            assert nested.session_id == "session-1"
            assert nested.correlation_id == "corr-1"
            assert nested.turn_id == "turn-1"
        assert current_telemetry_context().turn_id is None

    assert current_telemetry_context() == TelemetryContext()


@pytest.mark.asyncio
async def test_context_propagates_to_async_tasks() -> None:
    async def read_context() -> TelemetryContext:
        await asyncio.sleep(0)
        return current_telemetry_context()

    with bind_telemetry_context(correlation_id="corr-async", task_id="task-1"):
        child_context = await asyncio.create_task(read_context())

    assert child_context.correlation_id == "corr-async"
    assert child_context.task_id == "task-1"


def test_generated_correlation_ids_are_unique_and_opaque() -> None:
    first = new_correlation_id()
    second = new_correlation_id()

    assert first != second
    assert first.startswith("corr_")
    assert len(first) == len(second)


def test_json_logging_emits_contract_and_context_fields() -> None:
    with bind_telemetry_context(
        correlation_id="corr-1",
        session_id="session-1",
        turn_id="turn-1",
        agent_id="main",
    ):
        payload = _emit_json_log(
            event=EventName.AGENT_TURN_STARTED,
            outcome="started",
            model="test-model",
            duration_ms=12.5,
        )

    assert payload["event"] == "agent.turn.started"
    assert payload["level"] == "info"
    assert payload["correlation_id"] == "corr-1"
    assert payload["session_id"] == "session-1"
    assert payload["turn_id"] == "turn-1"
    assert payload["agent_id"] == "main"
    assert payload["outcome"] == "started"
    assert payload["model"] == "test-model"
    assert payload["duration_ms"] == 12.5
    assert str(payload["timestamp"]).endswith("Z")


def test_json_logging_redacts_secrets_and_rejects_content_fields() -> None:
    payload = _emit_json_log(
        event=EventName.LLM_REQUEST_FAILED,
        error_code=ErrorCode.PROVIDER_UNAVAILABLE,
        authorization="Bearer secret-token",
        prompt="private user prompt",
        user_input="private user input",
        tool_output="private tool output",
        provider="api_key=sk-thisisasecretvalue",
        arbitrary_unbounded_field="must not be emitted",
    )
    serialized = json.dumps(payload)

    assert payload["authorization"] == "[REDACTED]"
    assert payload["prompt"] == "[REDACTED]"
    assert payload["user_input"] == "[REDACTED]"
    assert payload["tool_output"] == "[REDACTED]"
    assert payload["provider"] == "api_key=[REDACTED]"
    assert "arbitrary_unbounded_field" not in payload
    assert "private user" not in serialized
    assert "secret-token" not in serialized
    assert "sk-thisisasecretvalue" not in serialized


def test_exception_logging_does_not_emit_exception_message_or_stack() -> None:
    stream = io.StringIO()
    configure_logging(json_output=True, stream=stream, force=True)
    try:
        raise RuntimeError("Authorization: Bearer do-not-log-this")
    except RuntimeError:
        logging.getLogger("test.telemetry").exception(
            "provider request failed",
            extra={
                "event": EventName.LLM_REQUEST_FAILED,
                "error_code": ErrorCode.PROVIDER_UNAVAILABLE,
            },
        )

    payload = json.loads(stream.getvalue())
    assert payload["exception_type"] == "RuntimeError"
    assert "do-not-log-this" not in stream.getvalue()
    assert "Traceback" not in stream.getvalue()


def test_readable_logging_uses_same_contract_without_sensitive_content() -> None:
    stream = io.StringIO()
    configure_logging(json_output=False, stream=stream, force=True)
    with bind_telemetry_context(correlation_id="corr-readable"):
        logging.getLogger("test.telemetry").warning(
            "tool failed with api_key=sk-thisisasecretvalue",
            extra={
                "event": EventName.TOOL_EXECUTION_FAILED,
                "error_code": ErrorCode.TOOL_EXECUTION_FAILED,
                "prompt": "private prompt",
            },
        )

    output = stream.getvalue()
    assert "tool.execution.failed" in output
    assert "corr-readable" in output
    assert "TOOL_EXECUTION_FAILED" in output
    assert "private prompt" not in output
    assert "sk-thisisasecretvalue" not in output


def test_default_logging_suppresses_legacy_info_noise_but_keeps_contract_events() -> None:
    stream = io.StringIO()
    configure_logging(json_output=True, stream=stream, force=True)
    logger = logging.getLogger("test.telemetry")

    logger.info("legacy diagnostic without an event")
    logger.info(
        "agent turn started",
        extra={"event": EventName.AGENT_TURN_STARTED, "outcome": "started"},
    )

    payloads = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [payload["event"] for payload in payloads] == ["agent.turn.started"]


def test_logging_initialization_also_configures_optional_opentelemetry(monkeypatch) -> None:
    configured: list[bool] = []
    monkeypatch.setattr(
        "voice_code.telemetry.logging.configure_opentelemetry",
        lambda: configured.append(True),
    )

    configure_logging(stream=io.StringIO())

    assert configured == [True]


def test_optional_opentelemetry_configuration_failure_does_not_break_logging(
    monkeypatch,
) -> None:
    def fail_configuration() -> None:
        raise RuntimeError("invalid private exporter configuration")

    monkeypatch.setattr(
        "voice_code.telemetry.logging.configure_opentelemetry",
        fail_configuration,
    )

    stream = io.StringIO()
    configure_logging(stream=stream)

    assert "private exporter configuration" not in stream.getvalue()
