from __future__ import annotations

from voice_code.audit import AuditRecorder, capture_audit_events, record_audit_event
from voice_code.telemetry import bind_telemetry_context

SECRET = "sk-secret-value"
PROMPT = "user prompt body must not appear"
OUTPUT = "full tool output must not appear"


def test_audit_event_uses_structured_allowlist_and_redacts_content() -> None:
    recorder = AuditRecorder()

    event = recorder.record(
        event_type="bash.write",
        actor="agent",
        resource_id="tool:bash",
        outcome="denied",
        rule="bash_write_intent_requires_approval",
        approval_result="deny",
        content=PROMPT,
        prompt=PROMPT,
        tool_output=OUTPUT,
        api_key=SECRET,
    )

    assert set(event) == {
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
    serialized = str(event)
    assert PROMPT not in serialized
    assert OUTPUT not in serialized
    assert SECRET not in serialized


def test_audit_event_correlation_links_session_turn_agent_rule_and_approval() -> None:
    with capture_audit_events() as events:
        with bind_telemetry_context(
            correlation_id="corr-1",
            session_id="session-1",
            turn_id="turn-1",
            agent_id="agent-1",
        ):
            record_audit_event(
                event_type="permission.decision",
                actor="agent",
                resource_id="tool:bash",
                outcome="denied",
                rule="dangerous_bash_pattern",
                approval_result="deny",
            )

    assert events == [
        {
            "event_type": "permission.decision",
            "actor": "agent",
            "timestamp": events[0]["timestamp"],
            "resource_id": "tool:bash",
            "outcome": "denied",
            "correlation_id": "corr-1",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "agent_id": "agent-1",
            "rule": "dangerous_bash_pattern",
            "approval_result": "deny",
        }
    ]


def test_high_risk_audit_event_types_are_available() -> None:
    with capture_audit_events() as events:
        for event_type in (
            "bash.write",
            "permission.decision",
            "mcp.first_connection",
            "goal.state_changed",
            "memory.extraction",
        ):
            record_audit_event(
                event_type=event_type,
                actor="agent",
                resource_id=f"resource:{event_type}",
                outcome="recorded",
            )

    assert [event["event_type"] for event in events] == [
        "bash.write",
        "permission.decision",
        "mcp.first_connection",
        "goal.state_changed",
        "memory.extraction",
    ]
