"""Browser permission approval for demo sessions."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from voice_code.permissions import (
    PermissionBehavior,
    PermissionDecision,
    PermissionReason,
    PermissionReasonType,
    PermissionRequest,
)
from voice_code.security import redact_secrets
from voice_code.web_demo.protocol import PermissionResolvePayload, WebDemoEvent
from voice_code.web_demo.sandbox import DemoSandbox, SandboxBoundaryError


@dataclass(slots=True)
class _PendingPermission:
    request: PermissionRequest
    event: threading.Event
    decision: PermissionDecision | None = None


class WebPermissionApprover:
    """Synchronous PermissionApprover that waits for a browser decision."""

    def __init__(
        self,
        *,
        emit: Callable[[WebDemoEvent], None],
        sandbox: DemoSandbox,
        session_id: str,
        timeout_seconds: float = 300.0,
    ) -> None:
        self._emit = emit
        self._sandbox = sandbox
        self._session_id = session_id
        self._timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingPermission] = {}

    def approve(self, request: PermissionRequest) -> PermissionDecision:
        request_id = f"perm_{uuid.uuid4().hex}"
        pending = _PendingPermission(request=request, event=threading.Event())
        with self._lock:
            self._pending[request_id] = pending

        self._emit(self._event_for(request_id, request))

        if not pending.event.wait(self._timeout_seconds):
            self.resolve(
                PermissionResolvePayload(
                    request_id=request_id,
                    behavior="deny",
                    message="Permission request timed out.",
                )
            )
            pending.event.wait(0)

        with self._lock:
            self._pending.pop(request_id, None)

        return pending.decision or _deny_decision(request, "Permission request was not resolved.")

    def resolve(self, payload: PermissionResolvePayload) -> bool:
        with self._lock:
            pending = self._pending.get(payload.request_id)
        if pending is None:
            return False

        if payload.behavior == "allow":
            pending.decision = PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=payload.message or _allow_message(payload.remember_scope),
                remember_scope=payload.remember_scope,
                reason=_reason_for(pending.request, allowed=True),
                matched_rule=pending.request.matched_rule,
                rule_source=pending.request.rule_source,
            )
        else:
            pending.decision = _deny_decision(
                pending.request,
                payload.message or "Permission denied by user.",
            )
        pending.event.set()
        return True

    def deny_all_pending(self, message: str = "Permission request was cancelled.") -> int:
        with self._lock:
            items = list(self._pending.items())
        for request_id, pending in items:
            pending.decision = _deny_decision(pending.request, message)
            pending.event.set()
            with self._lock:
                self._pending.pop(request_id, None)
        return len(items)

    def _event_for(self, request_id: str, request: PermissionRequest) -> WebDemoEvent:
        payload: dict[str, object] = {
            "requestId": request_id,
            "toolName": request.tool_name,
            "reason": request.reason,
            "isDestructive": request.is_destructive,
            "riskCategory": request.risk_category,
            "reasonType": request.reason_type,
            "matchedRule": request.matched_rule,
            "ruleSource": request.rule_source,
        }
        file_preview = _file_preview(self._sandbox, request.tool_input)
        if file_preview:
            payload["filePreview"] = file_preview
        command_preview = _command_preview(self._sandbox, request.tool_input)
        if command_preview:
            payload["commandPreview"] = command_preview
        return WebDemoEvent(
            event="permission.request",
            session_id=request.session_id or self._session_id,
            payload=payload,
        )


def _file_preview(sandbox: DemoSandbox, tool_input: dict[str, object]) -> str:
    for key in ("file_path", "path", "target_path", "cwd"):
        raw_value = str(tool_input.get(key, "")).strip()
        if not raw_value:
            continue
        try:
            resolved = sandbox.resolve_path(raw_value, allow_missing=True)
            return str(resolved.relative_to(sandbox.path))
        except (SandboxBoundaryError, ValueError):
            return Path(raw_value).name or "[outside sandbox]"
    return ""


def _command_preview(sandbox: DemoSandbox, tool_input: dict[str, object]) -> str:
    command = str(tool_input.get("command", "")).strip()
    if not command:
        return ""
    redacted = redact_secrets({"command": command})
    if isinstance(redacted, dict):
        command = str(redacted.get("command", command))
    command = command.replace(str(sandbox.path), ".")
    normalized = " ".join(command.split())
    if len(normalized) <= 180:
        return normalized
    return normalized[:179] + "…"


def _allow_message(scope: str) -> str:
    if scope == "session":
        return "Allowed for the rest of this session."
    if scope == "workspace":
        return "Allowed for this workspace."
    return "Permission allowed by user."


def _deny_decision(request: PermissionRequest, message: str) -> PermissionDecision:
    return PermissionDecision(
        behavior=PermissionBehavior.DENY,
        message=message,
        reason=_reason_for(request, allowed=False, message=message),
        matched_rule=request.matched_rule,
        rule_source=request.rule_source,
    )


def _reason_for(
    request: PermissionRequest,
    *,
    allowed: bool,
    message: str | None = None,
) -> PermissionReason:
    reason_type = (
        PermissionReasonType.SESSION_ALLOW
        if allowed
        else PermissionReasonType.RULE_MATCH
    )
    return PermissionReason(
        type=reason_type,
        message=message or request.reason or _allow_message(""),
        risk_category=request.risk_category or "medium",
    )
