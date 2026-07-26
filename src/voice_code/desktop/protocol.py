"""Typed protocol objects for the local desktop bridge."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from voice_code.agent.types import AgentEvent, EventType
from voice_code.permissions import PermissionRequest
from voice_code.security import redact_secrets

BridgeEventName = Literal[
    "agent.turn.started",
    "agent.text.delta",
    "agent.reasoning.delta",
    "agent.tool.call",
    "agent.tool.result",
    "agent.error",
    "agent.turn.finish",
    "permission.request",
    "session.updated",
]

PermissionResolveBehavior = Literal["allow", "deny"]
PermissionRememberScope = Literal["", "session", "workspace"]
BridgeEmitter = Callable[["BridgeEvent"], None | Awaitable[None]]
BridgeRequestMethod = Literal[
    "app.bootstrap",
    "session.archive",
    "session.create",
    "session.resume",
    "git.checkout",
    "model.select",
    "model.configure",
    "turn.start",
    "turn.interrupt",
    "permission.resolve",
]


class BridgeEvent(BaseModel):
    """A stable event envelope consumed by the desktop frontend."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["event"] = "event"
    event: BridgeEventName
    session_id: str = Field(alias="sessionId")
    turn_id: int | None = Field(default=None, alias="turnId")
    payload: dict[str, object] = Field(default_factory=dict)


class TurnStartResult(BaseModel):
    """Result returned once a bridge turn has completed."""

    session_id: str
    turn_id: int
    finish_reason: str = ""


class PermissionResolvePayload(BaseModel):
    """UI decision for a pending permission request."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(alias="requestId")
    behavior: PermissionResolveBehavior
    remember_scope: PermissionRememberScope = Field(default="", alias="rememberScope")
    message: str = ""


class BridgeRequest(BaseModel):
    """A request sent by a desktop client over any transport."""

    type: Literal["request"] = "request"
    id: str
    method: BridgeRequestMethod
    token: str
    payload: dict[str, object] = Field(default_factory=dict)


class BridgeError(BaseModel):
    code: str
    message: str


class BridgeResponse(BaseModel):
    """A stable response envelope for desktop bridge requests."""

    type: Literal["response"] = "response"
    id: str = ""
    ok: bool
    payload: dict[str, object] | None = None
    error: BridgeError | None = None


def map_agent_event(session_id: str, event: AgentEvent) -> BridgeEvent:
    """Map internal AgentEvent values to stable desktop event names."""
    if event.type == EventType.TEXT:
        return BridgeEvent(
            event="agent.text.delta",
            session_id=session_id,
            turn_id=event.turn,
            payload={"content": event.content},
        )
    if event.type == EventType.REASONING:
        return BridgeEvent(
            event="agent.reasoning.delta",
            session_id=session_id,
            turn_id=event.turn,
            payload={"content": event.content},
        )
    if event.type == EventType.TOOL_CALL:
        return BridgeEvent(
            event="agent.tool.call",
            session_id=session_id,
            turn_id=event.turn,
            payload={
                "toolCallId": event.tool_call_id,
                "toolName": event.tool_name,
                "toolArgs": _safe_payload(event.tool_args),
            },
        )
    if event.type == EventType.TOOL_RESULT:
        return BridgeEvent(
            event="agent.tool.result",
            session_id=session_id,
            turn_id=event.turn,
            payload={
                "toolCallId": event.tool_call_id,
                "toolName": event.tool_name,
                "toolResult": event.tool_result,
                "resultPreview": _preview_text(event.tool_result),
            },
        )
    if event.type == EventType.ERROR:
        if event.tool_call_id:
            return BridgeEvent(
                event="agent.tool.result",
                session_id=session_id,
                turn_id=event.turn,
                payload={
                    "toolCallId": event.tool_call_id,
                    "toolName": event.tool_name,
                    "toolResult": event.content,
                    "resultPreview": _preview_text(event.content),
                    "status": "error",
                },
            )
        return BridgeEvent(
            event="agent.error",
            session_id=session_id,
            turn_id=event.turn,
            payload={
                "content": event.content,
                "status": event.status,
                "errorCode": event.error_code,
            },
        )
    if event.type == EventType.FINISH:
        return BridgeEvent(
            event="agent.turn.finish",
            session_id=session_id,
            turn_id=event.turn,
            payload={
                "finishReason": event.finish_reason,
                "content": event.content,
            },
        )
    return BridgeEvent(
        event="agent.error",
        session_id=session_id,
        turn_id=event.turn,
        payload={"content": f"Unsupported agent event: {event.type.name}"},
    )


def make_turn_started_event(*, session_id: str, turn_id: int, user_text: str) -> BridgeEvent:
    return BridgeEvent(
        event="agent.turn.started",
        session_id=session_id,
        turn_id=turn_id,
        payload={"userText": user_text},
    )


def make_permission_request_event(
    *,
    session_id: str,
    request_id: str,
    request: PermissionRequest,
) -> BridgeEvent:
    return BridgeEvent(
        event="permission.request",
        session_id=session_id,
        payload={
            "requestId": request_id,
            "toolName": request.tool_name,
            "toolInput": _safe_payload(request.tool_input),
            "reason": request.reason,
            "isDestructive": request.is_destructive,
            "riskCategory": request.risk_category,
            "reasonType": request.reason_type,
            "matchedRule": request.matched_rule,
            "ruleSource": request.rule_source,
            "taskId": request.task_id or "",
            "agentType": request.agent_type or "",
        },
    )


def _safe_payload(payload: dict[str, object]) -> dict[str, object]:
    redacted = redact_secrets(payload)
    return redacted if isinstance(redacted, dict) else {}


def _preview_text(text: str, *, limit: int = 160) -> str:
    normalized = _clean_tool_use_error(" ".join(text.split()))
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def _clean_tool_use_error(text: str) -> str:
    return text.replace("<tool_use_error>", "").replace("</tool_use_error>", "")
