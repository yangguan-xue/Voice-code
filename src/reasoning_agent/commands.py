"""Shared slash-command parsing and session helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.messages import BaseMessage

from reasoning_agent.session import (
    TranscriptReader,
    TranscriptWriter,
    get_session_path,
    list_sessions,
)

COMMON_HELP = "/help /sessions /resume <id> /quit"
TUI_EXTRA_HELP = " /clear /tools /perm /detail [n] /collapse /copy /copylast"


@dataclass
class ResumeSessionResult:
    session_id: str
    messages: list[BaseMessage]
    transcript_writer: TranscriptWriter


@dataclass
class ParsedCommand:
    name: Literal[
        "help",
        "quit",
        "sessions",
        "resume",
        "clear",
        "copy",
        "copylast",
        "tools",
        "perm",
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


def resume_session(session_id: str) -> ResumeSessionResult:
    path = get_session_path(session_id)
    if not path.exists():
        raise FileNotFoundError(session_id)
    messages = TranscriptReader(path).read_all()
    return ResumeSessionResult(
        session_id=session_id,
        messages=messages,
        transcript_writer=TranscriptWriter(path),
    )


def parse_command(text: str) -> ParsedCommand:
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
    if cmd == "/detail":
        turn_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        return ParsedCommand(name="detail", raw=text, args={"turn_id": turn_id})
    if cmd == "/collapse":
        return ParsedCommand(name="collapse", raw=text)
    return ParsedCommand(name="unknown", raw=text, args={"command": cmd})
