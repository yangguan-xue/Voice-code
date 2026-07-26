"""Shared slash-command parsing and session helpers."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Literal

from voice_code.session import (
    ResumeRuntimeResult,
    list_sessions,
    resume_runtime_session,
)
from voice_code.status import (
    format_status_area_lines,
    format_status_gap_lines,
    format_status_overview_lines,
)

COMMON_HELP = (
    "/help /sessions /resume <id> /tasks /task <id> /task-close "
    "/task-stop <id> /task-stop-all /task-copy /task-path "
    "/status /status-areas /status-gaps "
    "/goal --allow <path> --verify <command> [--max-iterations N] <objective> "
    "/goal-resume <id> /goal-status <id> /goal-stop <id> "
    "/memory [list|show|candidates|approve|reject|conflicts|resolve|why|edit|reindex|audit] "
    "/remember [scope] <text> /forget [scope] <id> "
    "/perm-add [session|workspace] <allow|deny|ask> field=value... "
    "/perm-rule [session|workspace] <n> /perm-delete [session|workspace] <n> "
    "/perm-edit [session|workspace] <n> <allow|deny|ask> [reason] "
    "/perm-edit [session|workspace] <n> field=value... /quit"
)
TUI_EXTRA_HELP = (
    " /clear /tools /perm /perm-rules /perm-clear [scope]"
    " /detail [n] /collapse /copy /copylast"
)

ResumeSessionResult = ResumeRuntimeResult


@dataclass
class ParsedCommand:
    name: Literal[
        "help",
        "quit",
        "sessions",
        "resume",
        "tasks",
        "task",
        "task_close",
        "task_stop",
        "task_stop_all",
        "task_copy",
        "task_path",
        "status",
        "status_areas",
        "status_gaps",
        "goal",
        "goal_resume",
        "goal_status",
        "goal_stop",
        "memory",
        "remember",
        "forget",
        "clear",
        "copy",
        "copylast",
        "tools",
        "perm",
        "perm_rules",
        "perm_clear",
        "perm_add",
        "perm_rule",
        "perm_delete",
        "perm_edit",
        "detail",
        "collapse",
        "unknown",
    ]
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""


def format_session_lines(limit: int = 10) -> list[str]:
    sessions = list_sessions(limit=limit)
    if not sessions:
        return ["No saved sessions."]
    return [
        f"{session['id']}  {session['created_at']}  "
        f"{str(session['title'])[:60]} ({session['message_count']})"
        for session in sessions
    ]


def resume_session(session_id: str) -> ResumeRuntimeResult:
    return resume_runtime_session(session_id)


def format_status_lines(kind: Literal["overview", "areas", "gaps"]) -> list[str]:
    if kind == "overview":
        return format_status_overview_lines()
    if kind == "areas":
        return format_status_area_lines()
    return format_status_gap_lines()


def format_help_lines(*, include_tui: bool = False) -> list[str]:
    lines = [
        "Commands",
        "General",
        "- /help",
        "- /sessions",
        "- /resume <id>",
        "- /quit",
        "",
        "Tasks",
        "- /tasks",
        "- /task <id>",
        "- /task-close",
        "- /task-stop <id>",
        "- /task-stop-all",
        "- /task-copy",
        "- /task-path",
        "",
        "Status",
        "- /status",
        "- /status-areas",
        "- /status-gaps",
        "",
        "Goals",
        "- /goal --allow <path> --verify <command> [--max-iterations N] <objective>",
        "- /goal-resume <id>",
        "- /goal-status <id>",
        "- /goal-stop <id>",
        "",
        "Memory",
        "- /memory [list|show <id>|candidates|approve <id>|reject <id>]",
        "- /memory [conflicts|resolve <id> --keep <memory_id>]",
        "- /memory [why <id>|edit <id> <text>|reindex|audit]",
        "- /remember [user|project] <text>",
        "- /forget [user|project] <id>",
        "",
        "Permissions",
        "- /perm",
        "- /perm-rules",
        "- /perm-clear [session|workspace|all]",
        "- /perm-add [session|workspace] <allow|deny|ask> field=value...",
        "- /perm-rule [session|workspace] <n>",
        "- /perm-delete [session|workspace] <n>",
        "- /perm-edit [session|workspace] <n> <allow|deny|ask> [reason]",
        "- /perm-edit [session|workspace] <n> field=value...",
    ]
    if include_tui:
        lines.extend(
            [
                "",
                "TUI only",
                "- /clear",
                "- /tools",
                "- /detail [n]",
                "- /collapse",
                "- /copy",
                "- /copylast",
            ]
        )
    return lines


def _parse_scope_and_index(parts: list[str]) -> dict[str, Any]:
    scope = "session"
    index_text = ""
    if len(parts) > 1:
        if parts[1].lower() in {"session", "workspace"}:
            scope = parts[1].lower()
            index_text = parts[2] if len(parts) > 2 else ""
        else:
            index_text = parts[1]
    index = int(index_text) if index_text.isdigit() else None
    return {"scope": scope, "index": index}


def _parse_perm_edit(parts: list[str]) -> dict[str, Any]:
    parsed = _parse_scope_and_index(parts)
    behavior_pos = 3 if len(parts) > 1 and parts[1].lower() in {"session", "workspace"} else 2
    remainder = parts[behavior_pos:] if len(parts) > behavior_pos else []
    updates: dict[str, str] = {}
    if remainder and all("=" in item for item in remainder):
        for item in remainder:
            key, value = item.split("=", 1)
            updates[key.strip().lower()] = value.strip()
        behavior = ""
        reason_message = ""
    else:
        behavior = remainder[0].lower() if remainder else ""
        reason_message = " ".join(remainder[1:]).strip()
    parsed["behavior"] = behavior
    parsed["reason_message"] = reason_message
    parsed["updates"] = updates
    return parsed


def _parse_perm_add(parts: list[str]) -> dict[str, Any]:
    scope = "session"
    behavior_pos = 1
    if len(parts) > 1 and parts[1].lower() in {"session", "workspace"}:
        scope = parts[1].lower()
        behavior_pos = 2
    behavior = parts[behavior_pos].lower() if len(parts) > behavior_pos else ""
    updates: dict[str, str] = {}
    for item in parts[behavior_pos + 1 :]:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        updates[key.strip().lower()] = value.strip()
    return {"scope": scope, "behavior": behavior, "updates": updates}


def parse_command(text: str) -> ParsedCommand:
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    cmd = parts[0].lower()
    if cmd in {"/exit", "/quit"}:
        return ParsedCommand(name="quit", raw=text)
    if cmd == "/help":
        return ParsedCommand(name="help", raw=text)
    if cmd == "/sessions":
        return ParsedCommand(name="sessions", raw=text)
    if cmd == "/resume":
        return ParsedCommand(
            name="resume",
            raw=text,
            args={"session_id": parts[1] if len(parts) > 1 else ""},
        )
    if cmd == "/tasks":
        return ParsedCommand(name="tasks", raw=text)
    if cmd == "/task":
        return ParsedCommand(
            name="task",
            raw=text,
            args={"task_id": parts[1] if len(parts) > 1 else ""},
        )
    if cmd == "/task-close":
        return ParsedCommand(name="task_close", raw=text)
    if cmd == "/task-stop":
        return ParsedCommand(
            name="task_stop",
            raw=text,
            args={"task_id": parts[1] if len(parts) > 1 else ""},
        )
    if cmd == "/task-stop-all":
        return ParsedCommand(name="task_stop_all", raw=text)
    if cmd == "/task-copy":
        return ParsedCommand(name="task_copy", raw=text)
    if cmd == "/task-path":
        return ParsedCommand(name="task_path", raw=text)
    if cmd == "/status":
        return ParsedCommand(name="status", raw=text)
    if cmd == "/status-areas":
        return ParsedCommand(name="status_areas", raw=text)
    if cmd == "/status-gaps":
        return ParsedCommand(name="status_gaps", raw=text)
    if cmd == "/goal":
        objective_parts: list[str] = []
        verification_commands: list[str] = []
        allowed_paths: list[str] = []
        max_iterations = 5
        index = 1
        while index < len(parts):
            part = parts[index]
            if part == "--verify" and index + 1 < len(parts):
                verification_commands.append(parts[index + 1])
                index += 2
                continue
            if part == "--allow" and index + 1 < len(parts):
                allowed_paths.append(parts[index + 1])
                index += 2
                continue
            if part == "--max-iterations" and index + 1 < len(parts):
                try:
                    max_iterations = max(1, min(20, int(parts[index + 1])))
                except ValueError:
                    max_iterations = 5
                index += 2
                continue
            objective_parts.append(part)
            index += 1
        return ParsedCommand(
            name="goal",
            raw=text,
            args={
                "objective": " ".join(objective_parts).strip(),
                "verification_commands": verification_commands,
                "allowed_paths": allowed_paths,
                "max_iterations": max_iterations,
            },
        )
    if cmd == "/goal-status":
        return ParsedCommand(
            name="goal_status",
            raw=text,
            args={"goal_id": parts[1] if len(parts) > 1 else ""},
        )
    if cmd == "/goal-resume":
        return ParsedCommand(
            name="goal_resume",
            raw=text,
            args={"goal_id": parts[1] if len(parts) > 1 else ""},
        )
    if cmd == "/goal-stop":
        return ParsedCommand(
            name="goal_stop",
            raw=text,
            args={"goal_id": parts[1] if len(parts) > 1 else ""},
        )
    if cmd == "/memory":
        action = parts[1].lower() if len(parts) > 1 else "list"
        keep_id = ""
        if "--keep" in parts:
            keep_index = parts.index("--keep") + 1
            keep_id = parts[keep_index] if keep_index < len(parts) else ""
        return ParsedCommand(
            name="memory",
            raw=text,
            args={
                "action": action,
                "entry_id": parts[2] if len(parts) > 2 else "",
                "text": " ".join(parts[3:]).strip(),
                "keep_id": keep_id,
            },
        )
    if cmd == "/remember":
        scope = "project"
        content_start = 1
        if len(parts) > 1 and parts[1].lower() in {"user", "project"}:
            scope = parts[1].lower()
            content_start = 2
        return ParsedCommand(
            name="remember",
            raw=text,
            args={"scope": scope, "text": " ".join(parts[content_start:]).strip()},
        )
    if cmd == "/forget":
        scope = "project"
        id_pos = 1
        if len(parts) > 1 and parts[1].lower() in {"user", "project"}:
            scope = parts[1].lower()
            id_pos = 2
        return ParsedCommand(
            name="forget",
            raw=text,
            args={"scope": scope, "entry_id": parts[id_pos] if len(parts) > id_pos else ""},
        )
    if cmd == "/clear":
        return ParsedCommand(name="clear", raw=text)
    if cmd == "/copy":
        return ParsedCommand(name="copy", raw=text)
    if cmd == "/copylast":
        return ParsedCommand(name="copylast", raw=text)
    if cmd == "/tools":
        return ParsedCommand(name="tools", raw=text)
    if cmd == "/perm":
        return ParsedCommand(name="perm", raw=text)
    if cmd == "/perm-rules":
        return ParsedCommand(name="perm_rules", raw=text)
    if cmd == "/perm-clear":
        return ParsedCommand(
            name="perm_clear",
            raw=text,
            args={"scope": parts[1] if len(parts) > 1 else "session"},
        )
    if cmd == "/perm-add":
        return ParsedCommand(name="perm_add", raw=text, args=_parse_perm_add(parts))
    if cmd == "/perm-rule":
        return ParsedCommand(name="perm_rule", raw=text, args=_parse_scope_and_index(parts))
    if cmd == "/perm-delete":
        return ParsedCommand(name="perm_delete", raw=text, args=_parse_scope_and_index(parts))
    if cmd == "/perm-edit":
        return ParsedCommand(name="perm_edit", raw=text, args=_parse_perm_edit(parts))
    if cmd == "/detail":
        turn_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        return ParsedCommand(name="detail", raw=text, args={"turn_id": turn_id})
    if cmd == "/collapse":
        return ParsedCommand(name="collapse", raw=text)
    return ParsedCommand(name="unknown", raw=text, args={"command": cmd})
