"""Permission approval bridge for the desktop UI."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from voice_code.desktop.protocol import (
    BridgeEvent,
    PermissionResolvePayload,
    make_permission_request_event,
)
from voice_code.permissions import (
    PermissionBehavior,
    PermissionDecision,
    PermissionReason,
    PermissionReasonType,
    PermissionRequest,
)


@dataclass(slots=True)
class _PendingPermission:
    request: PermissionRequest
    event: threading.Event
    decision: PermissionDecision | None = None


class DesktopPermissionApprover:
    """Synchronous PermissionApprover that waits for a desktop UI decision."""

    def __init__(
        self,
        *,
        emit: Callable[[BridgeEvent], None],
        session_id: str = "",
        timeout_seconds: float = 300.0,
    ) -> None:
        self._emit = emit
        self._session_id = session_id
        self._timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingPermission] = {}

    def approve(self, request: PermissionRequest) -> PermissionDecision:
        request_id = f"perm_{uuid.uuid4().hex}"
        pending = _PendingPermission(request=request, event=threading.Event())
        with self._lock:
            self._pending[request_id] = pending

        self._emit(
            make_permission_request_event(
                session_id=request.session_id or self._session_id,
                request_id=request_id,
                request=request,
            )
        )

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

    def switch_session(self, session_id: str) -> None:
        self.deny_all_pending("Session changed.")
        self._session_id = session_id


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
