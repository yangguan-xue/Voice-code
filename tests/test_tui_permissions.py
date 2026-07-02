"""TUI permission policy tests."""

from __future__ import annotations

from voice_code.permissions import PermissionBehavior, PermissionDecision, can_use_tool
from voice_code.tui_permission_dialog import PendingPermissionRequest, _permission_source_label
from voice_code.tui_permissions import make_tui_permission_context


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


def test_permission_source_label_shows_subagent_origin():
    from voice_code.permissions import PermissionRequest

    request = PermissionRequest(
        tool_name="write",
        tool_input={"file_path": "/tmp/f.txt"},
        task_id="task-1",
        agent_type="researcher",
    )

    assert _permission_source_label(request) == "来源: 子 agent researcher  ·  task task-1"


def test_permission_dialog_request_carries_structured_reason_fields():
    from voice_code.permissions import PermissionRequest

    request = PermissionRequest(
        tool_name="bash",
        tool_input={"command": "pytest && ruff check"},
        reason="Compound Bash command requires explicit approval.",
        reason_type="compound_command_requires_approval",
        risk_category="medium",
        rule_source="built_in",
    )

    assert request.reason_type == "compound_command_requires_approval"
    assert request.risk_category == "medium"
    assert request.rule_source == "built_in"


def test_tui_workspace_allow_decision_can_carry_scope():
    decision = PermissionDecision(
        behavior=PermissionBehavior.ALLOW,
        message="Allowed for this workspace.",
        remember_scope="workspace",
    )

    assert decision.remember_scope == "workspace"
