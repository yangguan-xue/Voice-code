"""Voice-facing adapter for low-interruption delegation."""

from __future__ import annotations

from voice_code.delegation import DelegationBrief, DelegationService
from voice_code.permissions import (
    PermissionBehavior,
    PermissionDecision,
    PermissionRequest,
)
from voice_code.session.manager import make_session_id


def create_voice_delegation_brief(
    text: str,
    *,
    workspace: str,
    branch: str = "",
    relevant_files: list[str] | None = None,
) -> DelegationBrief:
    """Create a conservative brief; a caller may enrich it with model output."""
    intent = text.strip()
    if not intent:
        raise ValueError("Delegation request cannot be empty")
    high_risk_words = ("push", "deploy", "发布", "部署", "删除", "reset", "rm ")
    requires_confirmation = any(word in intent.lower() for word in high_risk_words)
    return DelegationBrief(
        task_id=make_session_id().lower(),
        intent=intent,
        workspace=workspace,
        branch=branch,
        relevant_files=list(relevant_files or []),
        risk_level="high" if requires_confirmation else "low",
        confirmation_required=requires_confirmation,
        context_sources=["voice", "git_status"],
    )


class DelegationPermissionApprover:
    """Allow bounded worktree activity while rejecting high-risk operations."""

    def approve(self, request: PermissionRequest) -> PermissionDecision:
        command = str(request.tool_input.get("command", "")).lower()
        blocked_commands = (
            "git commit",
            "git push",
            "git reset",
            "rm -r",
            "rm -f",
            "deploy",
            "发布",
            "部署",
        )
        high_risk = (
            request.is_destructive
            or request.risk_category == "high"
            or any(item in command for item in blocked_commands)
        )
        return PermissionDecision(
            behavior=PermissionBehavior.DENY if high_risk else PermissionBehavior.ALLOW,
            message=(
                "Delegation blocked a high-risk operation."
                if high_risk
                else "Delegation policy allowed this isolated operation."
            ),
        )


__all__ = [
    "DelegationPermissionApprover",
    "DelegationService",
    "create_voice_delegation_brief",
]
