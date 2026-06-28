"""TUI-specific permission helpers.

P0 过渡方案：
- TUI 不再默认使用 bypassPermissions
- 先接入独立 approver 路径
- 安全请求默认放行，危险请求默认拒绝
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from reasoning_agent.permissions import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionRequest,
)
from reasoning_agent.tui_permission_dialog import PendingPermissionRequest


class TuiPermissionApprover:
    """Minimal TUI approver for P0.

    When a dialog callback is supplied, ASK requests are forwarded to the
    Textual UI and this approver blocks the agent worker thread until the user
    responds. Without a callback we keep the previous P0 fallback policy for
    tests and non-mounted use.
    """

    def __init__(
        self,
        submit_request: Callable[[PendingPermissionRequest], None] | None = None,
    ) -> None:
        self._submit_request = submit_request

    def approve(self, request: PermissionRequest) -> PermissionDecision:
        if self._submit_request is not None:
            pending = PendingPermissionRequest(
                request=request,
                event=threading.Event(),
            )
            self._submit_request(pending)
            pending.event.wait()
            return pending.decision or PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="Permission request was dismissed.",
            )

        if request.is_destructive:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=(
                    "Permission denied in TUI for destructive tool request. "
                    "Use `reasoning --plain` for interactive approval."
                ),
            )
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)


def make_tui_permission_context(
    submit_request: Callable[[PendingPermissionRequest], None] | None = None,
) -> PermissionContext:
    """Create the default TUI permission context."""
    return PermissionContext(
        mode="default",
        approver=TuiPermissionApprover(submit_request),
    )
