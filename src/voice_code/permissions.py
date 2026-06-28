"""权限系统。

P0 第一刀：将“权限判断”与“如何向用户询问”解耦。
当前仍保留原有 stdin 审批行为作为默认实现，避免破坏 CLI/TUI 现状。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Protocol

logger = logging.getLogger(__name__)


class PermissionBehavior(Enum):
    ALLOW = auto()
    DENY = auto()
    ASK = auto()


class OutputFormat(Enum):
    """用户回复格式。"""

    TEXT = auto()
    YAML = auto()
    JSON = auto()


@dataclass
class PermissionDecision:
    behavior: PermissionBehavior
    message: str = ""
    updated_input: dict[str, object] | None = None


@dataclass
class PermissionRequest:
    tool_name: str
    tool_input: dict[str, object]
    reason: str = ""
    is_destructive: bool = False
    session_id: str | None = None
    task_id: str | None = None
    agent_type: str | None = None
    parent_session_id: str | None = None


class PermissionApprover(Protocol):
    """可替换的权限审批接口。"""

    def approve(self, request: PermissionRequest) -> PermissionDecision:
        """处理 ASK 请求并返回最终决策。"""


@dataclass
class PermissionContext:
    mode: str = "default"  # default | acceptEdits | bypassPermissions | dontAsk
    session_whitelist: set[str] = field(default_factory=set)
    output_format: OutputFormat = OutputFormat.TEXT
    approver: PermissionApprover | None = None
    session_id: str | None = None
    task_id: str | None = None
    agent_type: str | None = None
    parent_session_id: str | None = None


@dataclass
class PermissionEvaluation:
    """纯权限判断结果。"""

    decision: PermissionDecision
    request: PermissionRequest | None = None


_DANGEROUS_PATTERNS: list[str] = [
    "rm -rf",
    "rm -r ",
    "rm -fr",
    "rm --recursive",
    "force push",
    "--force ",
    "push -f",
    "push --force",
    "git reset --hard",
    "git clean -f",
    "git clean -fd",
    "sudo ",
    "chmod 777",
    "mkfs.",
    "dd if=",
    "> /dev/sd",
    "shutdown",
    "reboot",
    ":(){ :|:& };:",
    "curl ",
    " | bash",
    "wget ",
    " | sh",
    "eval ",
    "exec ",
    "mv /",
]


def _is_dangerous_bash(command: str) -> bool:
    """检查 Bash 命令是否匹配危险黑名单。"""
    lowered = command.lower()
    for pattern in _DANGEROUS_PATTERNS:
        if pattern in lowered:
            return True
    return False


def evaluate_tool_permission(
    tool_name: str,
    tool_input: dict[str, object],
    tool_metadata: dict[str, object] | None = None,
    context: PermissionContext | None = None,
) -> PermissionEvaluation:
    """执行纯权限判断，不做任何交互。"""
    ctx = context or PermissionContext()
    meta = tool_metadata or {}

    if ctx.mode == "bypassPermissions":
        return PermissionEvaluation(
            decision=PermissionDecision(behavior=PermissionBehavior.ALLOW)
        )

    if tool_name in ctx.session_whitelist:
        return PermissionEvaluation(
            decision=PermissionDecision(behavior=PermissionBehavior.ALLOW)
        )

    is_readonly = bool(meta.get("is_readonly", False))
    if is_readonly:
        return PermissionEvaluation(
            decision=PermissionDecision(behavior=PermissionBehavior.ALLOW)
        )

    is_destructive = bool(meta.get("is_destructive", False))

    if ctx.mode == "acceptEdits" and tool_name in ("write", "edit"):
        return PermissionEvaluation(
            decision=PermissionDecision(behavior=PermissionBehavior.ALLOW)
        )

    if tool_name == "bash":
        command = str(tool_input.get("command", ""))
        if _is_dangerous_bash(command):
            return PermissionEvaluation(
                decision=PermissionDecision(behavior=PermissionBehavior.ASK),
                request=PermissionRequest(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    reason=f"Dangerous Bash command detected: {command[:100]}",
                    is_destructive=True,
                    session_id=ctx.session_id,
                    task_id=ctx.task_id,
                    agent_type=ctx.agent_type,
                    parent_session_id=ctx.parent_session_id,
                ),
            )

    if is_destructive:
        if ctx.mode == "dontAsk":
            return PermissionEvaluation(
                decision=PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message="Permission denied (dontAsk mode).",
                )
            )
        return PermissionEvaluation(
            decision=PermissionDecision(behavior=PermissionBehavior.ASK),
            request=PermissionRequest(
                tool_name=tool_name,
                tool_input=tool_input,
                reason="This tool may have destructive effects.",
                is_destructive=True,
                session_id=ctx.session_id,
                task_id=ctx.task_id,
                agent_type=ctx.agent_type,
                parent_session_id=ctx.parent_session_id,
            ),
        )

    if ctx.mode == "dontAsk":
        return PermissionEvaluation(
            decision=PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="Permission denied (dontAsk mode).",
            )
        )

    return PermissionEvaluation(
        decision=PermissionDecision(behavior=PermissionBehavior.ASK),
        request=PermissionRequest(
            tool_name=tool_name,
            tool_input=tool_input,
            session_id=ctx.session_id,
            task_id=ctx.task_id,
            agent_type=ctx.agent_type,
            parent_session_id=ctx.parent_session_id,
        ),
    )


def can_use_tool(
    tool_name: str,
    tool_input: dict[str, object],
    tool_metadata: dict[str, object] | None = None,
    context: PermissionContext | None = None,
) -> PermissionDecision:
    """兼容旧调用方的权限入口。

    对外保持旧接口不变，但内部改成：
      1. 纯判断 evaluate_tool_permission()
      2. 若需要 ASK，再交给 approver 处理
    """
    ctx = context or PermissionContext()
    evaluation = evaluate_tool_permission(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_metadata=tool_metadata,
        context=ctx,
    )
    decision = evaluation.decision
    if decision.behavior != PermissionBehavior.ASK:
        return decision

    request = evaluation.request or PermissionRequest(
        tool_name=tool_name,
        tool_input=tool_input,
        session_id=ctx.session_id,
        task_id=ctx.task_id,
        agent_type=ctx.agent_type,
        parent_session_id=ctx.parent_session_id,
    )
    approver = ctx.approver or StdinPermissionApprover()
    final_decision = approver.approve(request)
    if final_decision.behavior == PermissionBehavior.ALLOW and (
        "rest of this session" in final_decision.message.lower()
        or "session" in final_decision.message.lower()
    ):
        _add_to_whitelist(ctx, tool_name)
    return final_decision


def _add_to_whitelist(ctx: PermissionContext, tool_name: str) -> None:
    ctx.session_whitelist.add(tool_name)


class StdinPermissionApprover:
    """默认 stdin 审批器。"""

    def approve(self, request: PermissionRequest) -> PermissionDecision:
        return _ask_user(request)


def _ask_user(request: PermissionRequest) -> PermissionDecision:
    """通过 stdin 询问用户是否允许执行工具。

    y = 允许本次
    n = 拒绝
    a = 允许本次 + 记住（本次会话后续自动放行）
    """
    msg = f"\n[PERMISSION] Allow tool '{request.tool_name}' to execute?"
    if request.reason:
        msg += f"\n  {request.reason}"
    msg += "\n  (y)es / (n)o / (a)llow all in session: "

    try:
        import builtins

        raw = builtins.input(msg).strip().lower()
    except (EOFError, OSError):
        logger.warning("Non-interactive mode, denying tool '%s'", request.tool_name)
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message="Permission denied (non-interactive mode).",
        )

    if raw in ("y", "yes"):
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)
    if raw in ("a", "all", "allow"):
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Tool allowed for the rest of this session.",
        )
    return PermissionDecision(
        behavior=PermissionBehavior.DENY,
        message="Permission denied by user.",
    )
