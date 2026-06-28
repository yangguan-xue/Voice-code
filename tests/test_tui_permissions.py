"""TUI permission policy tests."""

from __future__ import annotations

from reasoning_agent.permissions import PermissionBehavior, PermissionDecision, can_use_tool
from reasoning_agent.tui_permission_dialog import PendingPermissionRequest
from reasoning_agent.tui_permissions import make_tui_permission_context


def test_tui_permission_context_allows_non_destructive_ask_requests():
    ctx = make_tui_permission_context()

    decision = can_use_tool(
        tool_name="write",
        tool_input={"file_path": "/tmp/f.txt", "content": "x"},
        tool_metadata={"is_readonly": False},
        context=ctx,
    )

    assert decision.behavior == PermissionBehavior.ALLOW


def test_tui_permission_context_denies_destructive_requests():
    ctx = make_tui_permission_context()

    decision = can_use_tool(
        tool_name="bash",
        tool_input={"command": "rm -rf /tmp/test"},
        tool_metadata={"is_readonly": False},
        context=ctx,
    )

    assert decision.behavior == PermissionBehavior.DENY
    assert "reasoning --plain" in decision.message


def test_tui_permission_context_can_delegate_to_dialog_callback():
    seen: list[PendingPermissionRequest] = []

    def approve_from_ui(pending: PendingPermissionRequest) -> None:
        seen.append(pending)
        pending.resolve(PermissionDecision(behavior=PermissionBehavior.ALLOW))

    ctx = make_tui_permission_context(approve_from_ui)

    decision = can_use_tool(
        tool_name="write",
        tool_input={"file_path": "/tmp/f.txt", "content": "x"},
        tool_metadata={"is_readonly": False},
        context=ctx,
    )

    assert decision.behavior == PermissionBehavior.ALLOW
    assert len(seen) == 1
    assert seen[0].request.tool_name == "write"
