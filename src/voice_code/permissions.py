"""权限系统。

Permission Engine V2:
- evaluator 负责结构化评估
- approver 只处理 ASK 交互
- 规则支持 built-in / workspace / session / runtime 分层
"""

from __future__ import annotations

import fnmatch
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum, StrEnum, auto
from pathlib import Path
from typing import Protocol

from voice_code.audit import record_audit_event
from voice_code.telemetry import ErrorCode, EventName

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


class PermissionReasonType(StrEnum):
    MODE = "mode"
    RULE_MATCH = "rule_match"
    DANGEROUS_PATTERN = "dangerous_pattern"
    COMPOUND_COMMAND_REQUIRES_APPROVAL = "compound_command_requires_approval"
    WRITE_INTENT = "write_intent"
    BACKGROUND_AGENT_RESTRICTION = "background_agent_restriction"
    READONLY = "readonly"
    SESSION_ALLOW = "session_allow"
    DESTRUCTIVE_TOOL = "destructive_tool"
    DEFAULT_NON_READONLY = "default_non_readonly"


class PermissionRuleSource(StrEnum):
    BUILTIN = "built_in"
    WORKSPACE = "workspace"
    SESSION = "session"
    RUNTIME = "runtime"


@dataclass(slots=True)
class PermissionReason:
    type: PermissionReasonType
    message: str
    risk_category: str = "low"


@dataclass(slots=True)
class PermissionRule:
    name: str
    behavior: PermissionBehavior
    source: PermissionRuleSource
    reason_type: PermissionReasonType
    reason_message: str
    risk_category: str = "medium"
    tool_names: tuple[str, ...] = ()
    path_patterns: tuple[str, ...] = ()
    command_patterns: tuple[str, ...] = ()
    agent_types: tuple[str, ...] = ()
    background: bool | None = None


@dataclass
class PermissionDecision:
    behavior: PermissionBehavior
    message: str = ""
    updated_input: dict[str, object] | None = None
    reason: PermissionReason | None = None
    matched_rule: str = ""
    rule_source: str = ""
    remember_scope: str = ""


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
    reason_type: str = ""
    risk_category: str = ""
    matched_rule: str = ""
    rule_source: str = ""


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
    workspace_root: str | None = None
    workspace_rules: list[PermissionRule] = field(default_factory=list)
    session_rules: list[PermissionRule] = field(default_factory=list)
    runtime_rules: list[PermissionRule] = field(default_factory=list)
    on_change: Callable[[PermissionContext], None] | None = None


@dataclass
class PermissionEvaluation:
    """纯权限判断结果。"""

    decision: PermissionDecision
    request: PermissionRequest | None = None
    matched_rule: PermissionRule | None = None
    reason: PermissionReason | None = None


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
    ":(){ :|:& };:",
    "curl ",
    " | bash",
    "wget ",
    " | sh",
    "eval ",
    "exec ",
    "mv /",
]
_COMPOUND_COMMAND_TOKENS = ("&&", "||", ";", "|")
_WRITE_INTENT_TOKENS = (">", ">>", "tee ", "sed -i", "perl -pi", "python -c", "cat >")
_WORKSPACE_RULES_VERSION = 1
_EDITABLE_RULE_FIELDS = (
    "name",
    "behavior",
    "reason_type",
    "reason_message",
    "risk_category",
    "tool_names",
    "path_patterns",
    "command_patterns",
    "agent_types",
    "background",
)
_MATCHER_RULE_FIELDS = (
    "tool_names",
    "path_patterns",
    "command_patterns",
    "agent_types",
    "background",
)


def _is_dangerous_bash(command: str) -> bool:
    """检查 Bash 命令是否匹配危险黑名单。"""
    lowered = command.lower()
    return any(pattern in lowered for pattern in _DANGEROUS_PATTERNS)


def _has_compound_command(command: str) -> bool:
    return any(token in command for token in _COMPOUND_COMMAND_TOKENS)


def _has_write_intent(command: str) -> bool:
    lowered = command.lower()
    return any(token in lowered for token in _WRITE_INTENT_TOKENS)


def _background_context(ctx: PermissionContext) -> bool:
    return bool(ctx.task_id or ctx.agent_type)


def _tool_path_candidates(tool_input: dict[str, object]) -> list[str]:
    candidates: list[str] = []
    for key in ("file_path", "path", "cwd", "target_path"):
        value = str(tool_input.get(key, "")).strip()
        if value:
            candidates.append(value)
    return candidates


def permission_rule_edit_usage() -> str:
    return (
        "Usage: /perm-edit [session|workspace] <index> <allow|deny|ask> [reason] "
        "| /perm-edit [session|workspace] <index> field=value... "
        f"Supported fields: {', '.join(_EDITABLE_RULE_FIELDS)}"
    )


def permission_rule_add_usage() -> str:
    return (
        "Usage: /perm-add [session|workspace] <allow|deny|ask> field=value... "
        f"Supported fields: {', '.join(_EDITABLE_RULE_FIELDS)}"
    )


def _supported_fields_message() -> str:
    return ", ".join(_EDITABLE_RULE_FIELDS)


def _parse_behavior_value(value: str) -> PermissionBehavior:
    try:
        return PermissionBehavior[value.strip().upper()]
    except KeyError as exc:
        raise ValueError(
            f"invalid behavior: {value}; allowed: allow, deny, ask"
        ) from exc


def _parse_reason_type_value(value: str) -> PermissionReasonType:
    try:
        return PermissionReasonType(value)
    except ValueError as exc:
        allowed = ", ".join(reason_type.value for reason_type in PermissionReasonType)
        raise ValueError(f"invalid reason_type: {value}; allowed: {allowed}") from exc


def _parse_background_value(value: str) -> bool | None:
    lowered = value.lower()
    if lowered in {"true", "yes", "1"}:
        return True
    if lowered in {"false", "no", "0"}:
        return False
    if lowered in {"none", "null", ""}:
        return None
    raise ValueError(f"invalid background: {value}; use true, false, or none")


def _apply_rule_updates(
    current: PermissionRule,
    *,
    behavior: str = "",
    reason_message: str | None = None,
    updates: dict[str, str] | None = None,
) -> PermissionRule:
    replace_kwargs: dict[str, object] = {}

    if behavior:
        replace_kwargs["behavior"] = _parse_behavior_value(behavior)

    if reason_message is not None:
        replace_kwargs["reason_message"] = reason_message.strip()

    for key, raw_value in (updates or {}).items():
        normalized_key = key.strip().lower()
        value = raw_value.strip()
        if normalized_key == "name":
            replace_kwargs["name"] = value
        elif normalized_key == "reason_type":
            replace_kwargs["reason_type"] = _parse_reason_type_value(value)
        elif normalized_key == "risk_category":
            replace_kwargs["risk_category"] = value
        elif normalized_key in {
            "tool_names",
            "path_patterns",
            "command_patterns",
            "agent_types",
        }:
            replace_kwargs[normalized_key] = tuple(
                item.strip() for item in value.split(",") if item.strip()
            )
        elif normalized_key == "background":
            replace_kwargs["background"] = _parse_background_value(value)
        elif normalized_key == "behavior":
            replace_kwargs["behavior"] = _parse_behavior_value(value)
        elif normalized_key == "reason_message":
            replace_kwargs["reason_message"] = value
        else:
            raise ValueError(
                "unsupported field:"
                f" {normalized_key}; supported fields: {_supported_fields_message()}"
            )

    if not replace_kwargs:
        raise ValueError("no updates provided; pass a behavior, reason, or editable field")

    return replace(current, **replace_kwargs)


def _matches_rule(
    rule: PermissionRule,
    *,
    tool_name: str,
    tool_input: dict[str, object],
    context: PermissionContext,
) -> bool:
    if rule.tool_names and tool_name not in rule.tool_names:
        return False
    if rule.agent_types and (context.agent_type or "") not in rule.agent_types:
        return False
    if rule.background is not None and _background_context(context) != rule.background:
        return False
    if rule.command_patterns:
        command = str(tool_input.get("command", ""))
        if not any(fnmatch.fnmatch(command, pattern) for pattern in rule.command_patterns):
            return False
    if rule.path_patterns:
        paths = _tool_path_candidates(tool_input)
        if not paths:
            return False
        if not any(
            fnmatch.fnmatch(path, pattern)
            for path in paths
            for pattern in rule.path_patterns
        ):
            return False
    return True


def _rule_to_dict(rule: PermissionRule) -> dict[str, object]:
    return {
        "name": rule.name,
        "behavior": rule.behavior.name,
        "source": rule.source.value,
        "reason_type": rule.reason_type.value,
        "reason_message": rule.reason_message,
        "risk_category": rule.risk_category,
        "tool_names": list(rule.tool_names),
        "path_patterns": list(rule.path_patterns),
        "command_patterns": list(rule.command_patterns),
        "agent_types": list(rule.agent_types),
        "background": rule.background,
    }


def _rule_from_dict(payload: dict[str, object]) -> PermissionRule:
    return PermissionRule(
        name=str(payload.get("name", "")).strip() or "restored_rule",
        behavior=PermissionBehavior[str(payload.get("behavior", "ASK"))],
        source=PermissionRuleSource(
            str(payload.get("source", PermissionRuleSource.SESSION.value))
        ),
        reason_type=PermissionReasonType(
            str(payload.get("reason_type", PermissionReasonType.RULE_MATCH.value))
        ),
        reason_message=str(payload.get("reason_message", "")).strip()
        or "Restored permission rule.",
        risk_category=str(payload.get("risk_category", "medium")).strip() or "medium",
        tool_names=tuple(str(item) for item in list(payload.get("tool_names", []) or [])),
        path_patterns=tuple(str(item) for item in list(payload.get("path_patterns", []) or [])),
        command_patterns=tuple(
            str(item) for item in list(payload.get("command_patterns", []) or [])
        ),
        agent_types=tuple(str(item) for item in list(payload.get("agent_types", []) or [])),
        background=payload.get("background") if "background" in payload else None,
    )


def export_permission_state(context: PermissionContext) -> dict[str, object]:
    return {
        "session_rules": [_rule_to_dict(rule) for rule in context.session_rules],
    }


def load_session_rules_from_state(
    permission_state: dict[str, object] | None,
) -> list[PermissionRule]:
    if not permission_state:
        return []
    raw_rules = permission_state.get("session_rules", [])
    if not isinstance(raw_rules, list):
        return []
    rules: list[PermissionRule] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        try:
            rules.append(_rule_from_dict(item))
        except Exception:
            continue
    return rules


def get_workspace_rules_path(workspace_root: str) -> Path:
    return Path(workspace_root) / ".reasoning" / "permission-rules.json"


def load_workspace_rules(workspace_root: str | None) -> list[PermissionRule]:
    if not workspace_root:
        return []
    path = get_workspace_rules_path(workspace_root)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_rules = payload.get("rules", []) if isinstance(payload, dict) else []
    if not isinstance(raw_rules, list):
        return []
    rules: list[PermissionRule] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        try:
            rules.append(_rule_from_dict(item))
        except Exception:
            continue
    return rules


def save_workspace_rules(workspace_root: str | None, rules: list[PermissionRule]) -> None:
    if not workspace_root:
        return
    path = get_workspace_rules_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _WORKSPACE_RULES_VERSION,
        "rules": [_rule_to_dict(rule) for rule in rules],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def permission_rule_summary(context: PermissionContext) -> list[str]:
    lines = [f"mode: {context.mode}"]
    lines.append(f"session rules: {len(context.session_rules)}")
    lines.append(f"workspace rules: {len(context.workspace_rules)}")
    if context.workspace_root:
        lines.append(f"workspace root: {context.workspace_root}")
    if context.session_rules:
        lines.append("session:")
        lines.extend(_format_rule_lines(context.session_rules))
    if context.workspace_rules:
        lines.append("workspace:")
        lines.extend(_format_rule_lines(context.workspace_rules))
    if len(lines) == 3 or (
        len(lines) == 4 and context.workspace_root
    ):
        lines.append("no persisted permission rules")
    return lines


def _format_rule_lines(rules: list[PermissionRule]) -> list[str]:
    lines: list[str] = []
    for index, rule in enumerate(rules, start=1):
        target = rule.tool_names[0] if rule.tool_names else "*"
        matcher = ""
        if rule.path_patterns:
            matcher = f" path={rule.path_patterns[0]}"
        elif rule.command_patterns:
            matcher = f" command={rule.command_patterns[0]}"
        lines.append(
            f"  {index}. [{rule.behavior.name.lower()}] {target}{matcher}  ({rule.name})"
        )
    return lines


def _rules_for_scope(context: PermissionContext, scope: str) -> list[PermissionRule]:
    normalized = scope.strip().lower()
    if normalized == "session":
        return context.session_rules
    if normalized == "workspace":
        return context.workspace_rules
    raise ValueError("scope must be 'session' or 'workspace'")


def _rule_at_index(context: PermissionContext, *, scope: str, index: int) -> PermissionRule:
    rules = _rules_for_scope(context, scope)
    if index < 1 or index > len(rules):
        raise IndexError(f"{scope} rule index out of range: {index}")
    return rules[index - 1]


def describe_permission_rule(
    context: PermissionContext,
    *,
    scope: str,
    index: int,
) -> list[str]:
    rule = _rule_at_index(context, scope=scope, index=index)
    lines = [f"{scope} rule {index}"]
    lines.append(f"name: {rule.name}")
    lines.append(f"behavior: {rule.behavior.name.lower()}")
    lines.append(f"source: {rule.source.value}")
    lines.append(f"reason_type: {rule.reason_type.value}")
    lines.append(f"risk_category: {rule.risk_category}")
    lines.append(f"reason_message: {rule.reason_message}")
    if rule.tool_names:
        lines.append(f"tool_names: {', '.join(rule.tool_names)}")
    if rule.path_patterns:
        lines.append(f"path_patterns: {', '.join(rule.path_patterns)}")
    if rule.command_patterns:
        lines.append(f"command_patterns: {', '.join(rule.command_patterns)}")
    if rule.agent_types:
        lines.append(f"agent_types: {', '.join(rule.agent_types)}")
    if rule.background is not None:
        lines.append(f"background: {rule.background}")
    return lines


def delete_permission_rule(
    context: PermissionContext,
    *,
    scope: str,
    index: int,
) -> PermissionRule:
    rules = _rules_for_scope(context, scope)
    if index < 1 or index > len(rules):
        raise IndexError(f"{scope} rule index out of range: {index}")
    removed = rules.pop(index - 1)
    if scope == "workspace":
        save_workspace_rules(context.workspace_root, context.workspace_rules)
    if context.on_change is not None:
        context.on_change(context)
    return removed


def update_permission_rule(
    context: PermissionContext,
    *,
    scope: str,
    index: int,
    behavior: str = "",
    reason_message: str | None = None,
    updates: dict[str, str] | None = None,
) -> PermissionRule:
    rules = _rules_for_scope(context, scope)
    if index < 1 or index > len(rules):
        raise IndexError(f"{scope} rule index out of range: {index}")

    current = rules[index - 1]
    updated = _apply_rule_updates(
        current,
        behavior=behavior,
        reason_message=reason_message,
        updates=updates,
    )
    rules[index - 1] = updated
    if scope == "workspace":
        save_workspace_rules(context.workspace_root, context.workspace_rules)
    if context.on_change is not None:
        context.on_change(context)
    return updated


def create_permission_rule(
    context: PermissionContext,
    *,
    scope: str,
    behavior: str,
    updates: dict[str, str] | None = None,
) -> PermissionRule:
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"session", "workspace"}:
        raise ValueError("scope must be 'session' or 'workspace'")

    source = (
        PermissionRuleSource.SESSION
        if normalized_scope == "session"
        else PermissionRuleSource.WORKSPACE
    )
    base_rule = PermissionRule(
        name=f"manual_{source.value}_{len(_rules_for_scope(context, normalized_scope)) + 1}",
        behavior=_parse_behavior_value(behavior),
        source=source,
        reason_type=PermissionReasonType.RULE_MATCH,
        reason_message="Manually added permission rule.",
    )
    created = _apply_rule_updates(base_rule, updates=updates)
    if not any(
        getattr(created, field_name)
        for field_name in _MATCHER_RULE_FIELDS
    ):
        raise ValueError(
            "at least one matcher field is required; add one of: "
            + ", ".join(_MATCHER_RULE_FIELDS)
        )

    rules = _rules_for_scope(context, normalized_scope)
    rules.append(created)
    if normalized_scope == "workspace":
        save_workspace_rules(context.workspace_root, context.workspace_rules)
    if context.on_change is not None:
        context.on_change(context)
    return created


def clear_permission_rules(
    context: PermissionContext,
    *,
    scope: str,
) -> tuple[int, int]:
    removed_session = 0
    removed_workspace = 0
    normalized = scope.strip().lower()
    if normalized in {"session", "all"}:
        removed_session = len(context.session_rules)
        context.session_rules.clear()
        context.session_whitelist.clear()
    if normalized in {"workspace", "all"}:
        removed_workspace = len(context.workspace_rules)
        context.workspace_rules.clear()
        save_workspace_rules(context.workspace_root, context.workspace_rules)
    if (removed_session or removed_workspace) and context.on_change is not None:
        context.on_change(context)
    return removed_session, removed_workspace


def _build_reason(rule: PermissionRule) -> PermissionReason:
    return PermissionReason(
        type=rule.reason_type,
        message=rule.reason_message,
        risk_category=rule.risk_category,
    )


def _build_decision(
    *,
    behavior: PermissionBehavior,
    reason: PermissionReason,
    matched_rule: PermissionRule | None = None,
    message: str | None = None,
) -> PermissionDecision:
    return PermissionDecision(
        behavior=behavior,
        message=message or reason.message,
        reason=reason,
        matched_rule=matched_rule.name if matched_rule is not None else "",
        rule_source=matched_rule.source.value if matched_rule is not None else "",
    )


def _build_request(
    *,
    tool_name: str,
    tool_input: dict[str, object],
    context: PermissionContext,
    reason: PermissionReason,
    matched_rule: PermissionRule | None,
    is_destructive: bool,
) -> PermissionRequest:
    return PermissionRequest(
        tool_name=tool_name,
        tool_input=tool_input,
        reason=reason.message,
        is_destructive=is_destructive,
        session_id=context.session_id,
        task_id=context.task_id,
        agent_type=context.agent_type,
        parent_session_id=context.parent_session_id,
        reason_type=reason.type.value,
        risk_category=reason.risk_category,
        matched_rule=matched_rule.name if matched_rule is not None else "",
        rule_source=matched_rule.source.value if matched_rule is not None else "",
    )


def _rule_priority(rule: PermissionRule) -> tuple[int, int, int]:
    behavior_rank = {
        PermissionBehavior.DENY: 3,
        PermissionBehavior.ASK: 2,
        PermissionBehavior.ALLOW: 1,
    }[rule.behavior]
    source_rank = {
        PermissionRuleSource.RUNTIME: 4,
        PermissionRuleSource.SESSION: 3,
        PermissionRuleSource.WORKSPACE: 2,
        PermissionRuleSource.BUILTIN: 1,
    }[rule.source]
    specificity_rank = (
        int(bool(rule.background is not None))
        + int(bool(rule.path_patterns))
        + int(bool(rule.command_patterns))
        + int(bool(rule.agent_types))
    )
    return (source_rank, behavior_rank, specificity_rank)


def _session_whitelist_rule(tool_name: str) -> PermissionRule:
    return PermissionRule(
        name=f"session_whitelist:{tool_name}",
        behavior=PermissionBehavior.ALLOW,
        source=PermissionRuleSource.SESSION,
        reason_type=PermissionReasonType.SESSION_ALLOW,
        reason_message=f"Tool '{tool_name}' was already allowed for this session.",
        risk_category="low",
        tool_names=(tool_name,),
    )


def _build_persistent_rule(
    request: PermissionRequest,
    *,
    source: PermissionRuleSource,
) -> PermissionRule:
    path_patterns: tuple[str, ...] = ()
    command_patterns: tuple[str, ...] = ()
    file_path = str(request.tool_input.get("file_path", "")).strip()
    command = str(request.tool_input.get("command", "")).strip()
    if file_path:
        path_patterns = (file_path,)
    elif command:
        command_patterns = (command,)
    return PermissionRule(
        name=f"{source.value}:{request.tool_name}:{request.reason_type or 'rule'}",
        behavior=PermissionBehavior.ALLOW,
        source=source,
        reason_type=PermissionReasonType.SESSION_ALLOW,
        reason_message=(
            "Tool allowed for this workspace."
            if source == PermissionRuleSource.WORKSPACE
            else "Tool allowed for this session."
        ),
        risk_category=request.risk_category or "medium",
        tool_names=(request.tool_name,),
        path_patterns=path_patterns,
        command_patterns=command_patterns,
    )


def _append_rule_if_missing(rules: list[PermissionRule], rule: PermissionRule) -> None:
    serialized = _rule_to_dict(rule)
    for existing in rules:
        if _rule_to_dict(existing) == serialized:
            return
    rules.append(rule)


def _built_in_rules(
    *,
    tool_name: str,
    tool_input: dict[str, object],
    tool_metadata: dict[str, object],
    context: PermissionContext,
) -> list[PermissionRule]:
    rules: list[PermissionRule] = []
    if tool_name in context.session_whitelist:
        rules.append(_session_whitelist_rule(tool_name))

    is_readonly = bool(tool_metadata.get("is_readonly", False))
    if is_readonly:
        rules.append(
            PermissionRule(
                name="readonly_auto_allow",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionRuleSource.BUILTIN,
                reason_type=PermissionReasonType.READONLY,
                reason_message="Readonly tool auto-allowed.",
                risk_category="low",
                tool_names=(tool_name,),
            )
        )

    if context.mode == "acceptEdits" and tool_name in ("write", "edit"):
        rules.append(
            PermissionRule(
                name="accept_edits_mode",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionRuleSource.BUILTIN,
                reason_type=PermissionReasonType.MODE,
                reason_message="acceptEdits mode allows write/edit tools.",
                risk_category="medium",
                tool_names=(tool_name,),
            )
        )

    if tool_name == "bash":
        command = str(tool_input.get("command", ""))
        if _is_dangerous_bash(command):
            rules.append(
                PermissionRule(
                    name="dangerous_bash_pattern",
                    behavior=PermissionBehavior.ASK,
                    source=PermissionRuleSource.BUILTIN,
                    reason_type=PermissionReasonType.DANGEROUS_PATTERN,
                    reason_message=f"Dangerous Bash command detected: {command[:100]}",
                    risk_category="high",
                    tool_names=("bash",),
                )
            )
        elif _has_compound_command(command):
            rules.append(
                PermissionRule(
                    name="compound_bash_requires_approval",
                    behavior=PermissionBehavior.ASK,
                    source=PermissionRuleSource.BUILTIN,
                    reason_type=PermissionReasonType.COMPOUND_COMMAND_REQUIRES_APPROVAL,
                    reason_message="Compound Bash command requires explicit approval.",
                    risk_category="medium",
                    tool_names=("bash",),
                )
            )
        elif _has_write_intent(command):
            rules.append(
                PermissionRule(
                    name="bash_write_intent_requires_approval",
                    behavior=PermissionBehavior.ASK,
                    source=PermissionRuleSource.BUILTIN,
                    reason_type=PermissionReasonType.WRITE_INTENT,
                    reason_message="Bash command appears to write or redirect data.",
                    risk_category="medium",
                    tool_names=("bash",),
                )
            )

    is_destructive = bool(tool_metadata.get("is_destructive", False))
    if is_destructive:
        rules.append(
            PermissionRule(
                name="destructive_tool_requires_approval",
                behavior=PermissionBehavior.ASK,
                source=PermissionRuleSource.BUILTIN,
                reason_type=PermissionReasonType.DESTRUCTIVE_TOOL,
                reason_message="This tool may have destructive effects.",
                risk_category="high",
                tool_names=(tool_name,),
            )
        )

    if _background_context(context) and is_destructive:
        rules.append(
            PermissionRule(
                name="background_destructive_requires_approval",
                behavior=PermissionBehavior.ASK,
                source=PermissionRuleSource.BUILTIN,
                reason_type=PermissionReasonType.BACKGROUND_AGENT_RESTRICTION,
                reason_message="Background/subagent destructive actions require explicit approval.",
                risk_category="high",
                tool_names=(tool_name,),
                background=True,
            )
        )

    if not is_readonly:
        rules.append(
            PermissionRule(
                name="default_non_readonly_requires_approval",
                behavior=PermissionBehavior.ASK,
                source=PermissionRuleSource.BUILTIN,
                reason_type=PermissionReasonType.DEFAULT_NON_READONLY,
                reason_message="Non-readonly tools require approval by default.",
                risk_category="medium",
                tool_names=(tool_name,),
            )
        )
    return rules


def _matched_rules(
    *,
    tool_name: str,
    tool_input: dict[str, object],
    tool_metadata: dict[str, object],
    context: PermissionContext,
) -> list[PermissionRule]:
    all_rules = [
        *context.runtime_rules,
        *context.session_rules,
        *context.workspace_rules,
        *_built_in_rules(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_metadata=tool_metadata,
            context=context,
        ),
    ]
    return [
        rule
        for rule in all_rules
        if _matches_rule(rule, tool_name=tool_name, tool_input=tool_input, context=context)
    ]


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
        reason = PermissionReason(
            type=PermissionReasonType.MODE,
            message="bypassPermissions mode allows this tool.",
            risk_category="low",
        )
        decision = _build_decision(behavior=PermissionBehavior.ALLOW, reason=reason)
        return PermissionEvaluation(decision=decision, reason=reason)

    if ctx.mode == "acceptEdits" and tool_name in {"write", "edit"}:
        reason = PermissionReason(
            type=PermissionReasonType.MODE,
            message="acceptEdits mode allows write/edit tools.",
            risk_category="medium",
        )
        decision = _build_decision(behavior=PermissionBehavior.ALLOW, reason=reason)
        return PermissionEvaluation(decision=decision, reason=reason)

    matched_rules = _matched_rules(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_metadata=meta,
        context=ctx,
    )
    if not matched_rules:
        reason = PermissionReason(
            type=PermissionReasonType.DEFAULT_NON_READONLY,
            message="Non-readonly tools require approval by default.",
            risk_category="medium",
        )
        decision = _build_decision(behavior=PermissionBehavior.ASK, reason=reason)
        request = _build_request(
            tool_name=tool_name,
            tool_input=tool_input,
            context=ctx,
            reason=reason,
            matched_rule=None,
            is_destructive=bool(meta.get("is_destructive", False)),
        )
        return PermissionEvaluation(decision=decision, request=request, reason=reason)

    matched_rule = max(matched_rules, key=_rule_priority)
    reason = _build_reason(matched_rule)
    behavior = matched_rule.behavior

    if ctx.mode == "dontAsk" and behavior == PermissionBehavior.ASK:
        deny_reason = PermissionReason(
            type=PermissionReasonType.MODE,
            message="Permission denied (dontAsk mode).",
            risk_category=reason.risk_category,
        )
        decision = _build_decision(
            behavior=PermissionBehavior.DENY,
            reason=deny_reason,
            matched_rule=matched_rule,
            message="Permission denied (dontAsk mode).",
        )
        return PermissionEvaluation(
            decision=decision,
            matched_rule=matched_rule,
            reason=deny_reason,
        )

    decision = _build_decision(
        behavior=behavior,
        reason=reason,
        matched_rule=matched_rule,
    )
    if behavior != PermissionBehavior.ASK:
        return PermissionEvaluation(
            decision=decision,
            matched_rule=matched_rule,
            reason=reason,
        )

    request = _build_request(
        tool_name=tool_name,
        tool_input=tool_input,
        context=ctx,
        reason=reason,
        matched_rule=matched_rule,
        is_destructive=bool(meta.get("is_destructive", False)) or reason.risk_category == "high",
    )
    return PermissionEvaluation(
        decision=decision,
        request=request,
        matched_rule=matched_rule,
        reason=reason,
    )


def can_use_tool(
    tool_name: str,
    tool_input: dict[str, object],
    tool_metadata: dict[str, object] | None = None,
    context: PermissionContext | None = None,
) -> PermissionDecision:
    """兼容旧调用方的权限入口。"""
    ctx = context or PermissionContext()
    evaluation = evaluate_tool_permission(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_metadata=tool_metadata,
        context=ctx,
    )
    decision = evaluation.decision
    if decision.behavior != PermissionBehavior.ASK:
        _log_permission_decision(tool_name, decision)
        return decision

    request = evaluation.request or PermissionRequest(
        tool_name=tool_name,
        tool_input=tool_input,
        session_id=ctx.session_id,
        task_id=ctx.task_id,
        agent_type=ctx.agent_type,
        parent_session_id=ctx.parent_session_id,
        reason=decision.message,
        reason_type=decision.reason.type.value if decision.reason is not None else "",
        risk_category=decision.reason.risk_category if decision.reason is not None else "",
        matched_rule=decision.matched_rule,
        rule_source=decision.rule_source,
    )
    approver = ctx.approver or StdinPermissionApprover()
    final_decision = approver.approve(request)
    if final_decision.reason is None and decision.reason is not None:
        final_decision.reason = decision.reason
    if not final_decision.matched_rule:
        final_decision.matched_rule = decision.matched_rule
    if not final_decision.rule_source:
        final_decision.rule_source = decision.rule_source
    if final_decision.behavior == PermissionBehavior.ALLOW and (
        "rest of this session" in final_decision.message.lower()
        or "session" in final_decision.message.lower()
    ):
        _add_to_whitelist(ctx, tool_name)
    remember_scope = final_decision.remember_scope.lower().strip()
    if remember_scope == "session":
        _append_rule_if_missing(
            ctx.session_rules,
            _build_persistent_rule(request, source=PermissionRuleSource.SESSION),
        )
        if ctx.on_change is not None:
            ctx.on_change(ctx)
    elif remember_scope == "workspace":
        _append_rule_if_missing(
            ctx.workspace_rules,
            _build_persistent_rule(request, source=PermissionRuleSource.WORKSPACE),
        )
        save_workspace_rules(ctx.workspace_root, ctx.workspace_rules)
        if ctx.on_change is not None:
            ctx.on_change(ctx)
    _log_permission_decision(tool_name, final_decision)
    return final_decision


def _log_permission_decision(tool_name: str, decision: PermissionDecision) -> None:
    reason = decision.reason
    outcome = decision.behavior.name.lower()
    extra: dict[str, object] = {
        "event": EventName.PERMISSION_DECIDED,
        "tool_name": tool_name,
        "permission_behavior": outcome,
        "outcome": outcome,
        "risk_category": reason.risk_category if reason is not None else "unknown",
        "rule_source": decision.rule_source or "none",
    }
    if decision.behavior == PermissionBehavior.DENY:
        extra["error_code"] = ErrorCode.TOOL_PERMISSION_DENIED
    logger.info("permission decided", extra=extra)
    record_audit_event(
        event_type="permission.decision",
        actor="agent",
        resource_id=f"tool:{tool_name}",
        outcome=outcome,
        rule=decision.matched_rule or decision.rule_source or "permission_policy",
        approval_result=outcome,
    )


def _add_to_whitelist(ctx: PermissionContext, tool_name: str) -> None:
    ctx.session_whitelist.add(tool_name)


class StdinPermissionApprover:
    """默认 stdin 审批器。"""

    def approve(self, request: PermissionRequest) -> PermissionDecision:
        return _ask_user(request)


def _ask_user(request: PermissionRequest) -> PermissionDecision:
    """通过 stdin 询问用户是否允许执行工具。"""
    msg = f"\n[PERMISSION] Allow tool '{request.tool_name}' to execute?"
    if request.reason:
        msg += f"\n  reason: {request.reason}"
    if request.reason_type:
        msg += f"\n  type: {request.reason_type}"
    if request.risk_category:
        msg += f"\n  risk: {request.risk_category}"
    if request.rule_source:
        msg += f"\n  source: {request.rule_source}"
    msg += "\n  (y)es / (n)o / (a)llow all in session / (w)orkspace: "

    try:
        import builtins

        raw = builtins.input(msg).strip().lower()
    except (EOFError, OSError):
        logger.warning("Non-interactive mode, denying tool '%s'", request.tool_name)
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message="Permission denied (non-interactive mode).",
            reason=PermissionReason(
                type=PermissionReasonType.MODE,
                message="Permission denied (non-interactive mode).",
                risk_category=request.risk_category or "medium",
            ),
            matched_rule=request.matched_rule,
            rule_source=request.rule_source,
        )

    if raw in ("y", "yes"):
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            reason=PermissionReason(
                type=PermissionReasonType.RULE_MATCH,
                message=request.reason or "Permission allowed by user.",
                risk_category=request.risk_category or "medium",
            ),
            matched_rule=request.matched_rule,
            rule_source=request.rule_source,
        )
    if raw in ("a", "all", "allow"):
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Tool allowed for the rest of this session.",
            reason=PermissionReason(
                type=PermissionReasonType.SESSION_ALLOW,
                message="Tool allowed for the rest of this session.",
                risk_category=request.risk_category or "medium",
            ),
            matched_rule=request.matched_rule,
            rule_source=request.rule_source or PermissionRuleSource.SESSION.value,
            remember_scope="session",
        )
    if raw in ("w", "workspace"):
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Tool allowed for this workspace.",
            reason=PermissionReason(
                type=PermissionReasonType.RULE_MATCH,
                message="Tool allowed for this workspace.",
                risk_category=request.risk_category or "medium",
            ),
            matched_rule=request.matched_rule,
            rule_source=PermissionRuleSource.WORKSPACE.value,
            remember_scope="workspace",
        )
    return PermissionDecision(
        behavior=PermissionBehavior.DENY,
        message="Permission denied by user.",
        reason=PermissionReason(
            type=PermissionReasonType.RULE_MATCH,
            message="Permission denied by user.",
            risk_category=request.risk_category or "medium",
        ),
        matched_rule=request.matched_rule,
        rule_source=request.rule_source,
    )
