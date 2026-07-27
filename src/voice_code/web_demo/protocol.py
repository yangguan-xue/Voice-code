"""Typed protocol contracts for the web demo bridge."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WebDemoEventName = Literal[
    "agent.turn.started",
    "agent.text.delta",
    "agent.reasoning.delta",
    "agent.tool.call",
    "agent.tool.result",
    "agent.error",
    "agent.turn.finish",
    "permission.request",
    "session.updated",
    "session.expired",
    "sandbox.diff.updated",
    "sandbox.reset",
]
WebDemoRequestMethod = Literal[
    "demo.session.create",
    "turn.start",
    "turn.interrupt",
    "permission.resolve",
    "sandbox.diff.get",
    "sandbox.file.get",
    "sandbox.reset",
    "session.discard",
]
PermissionResolveBehavior = Literal["allow", "deny"]
PermissionRememberScope = Literal["", "session", "workspace"]


class WebDemoEvent(BaseModel):
    """Stable event envelope consumed by the browser demo."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["event"] = "event"
    event: WebDemoEventName
    session_id: str = Field(alias="sessionId")
    turn_id: int | None = Field(default=None, alias="turnId")
    payload: dict[str, object] = Field(default_factory=dict)


class WebDemoRequest(BaseModel):
    """Request sent by the web demo frontend over WebSocket."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["request"] = "request"
    id: str
    method: WebDemoRequestMethod
    session_token: str = Field(default="", alias="sessionToken")
    payload: dict[str, object] = Field(default_factory=dict)


class WebDemoError(BaseModel):
    code: str
    message: str


class WebDemoResponse(BaseModel):
    """Stable response envelope for web demo requests."""

    type: Literal["response"] = "response"
    id: str = ""
    ok: bool
    payload: dict[str, object] | None = None
    error: WebDemoError | None = None


class PermissionResolvePayload(BaseModel):
    """Browser decision for a pending permission request."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(alias="requestId")
    behavior: PermissionResolveBehavior
    remember_scope: PermissionRememberScope = Field(default="", alias="rememberScope")
    message: str = ""


def error_response(code: str, message: str, *, request_id: str = "") -> WebDemoResponse:
    return WebDemoResponse(
        id=request_id,
        ok=False,
        error=WebDemoError(code=code, message=message),
    )
