"""Transcript JSONL reader/writer."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

_META_RECORD_TYPE = "__meta__"
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_WHITESPACE_RE = re.compile(r"\s+")


def _message_to_record(message: BaseMessage) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": message.type,
        "content": message.content,
    }
    if isinstance(message, AIMessage):
        record["tool_calls"] = getattr(message, "tool_calls", []) or []
    if isinstance(message, ToolMessage):
        record["tool_call_id"] = message.tool_call_id
        record["name"] = getattr(message, "name", "") or ""
    return record


def _record_to_message(record: dict[str, Any]) -> BaseMessage:
    msg_type = record.get("type", "")
    content = record.get("content", "")
    if msg_type == "system":
        return SystemMessage(content=content)
    if msg_type == "human":
        return HumanMessage(content=content)
    if msg_type == "ai":
        return AIMessage(content=content, tool_calls=record.get("tool_calls", []))
    if msg_type == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=str(record.get("tool_call_id", "")),
            name=str(record.get("name", "")),
        )
    raise ValueError(f"Unknown transcript message type: {msg_type}")


def _clean_title_line(line: str) -> str:
    normalized = line.strip()
    if not normalized:
        return ""
    if normalized.startswith("<") and normalized.endswith(">"):
        return ""
    if normalized.startswith("#"):
        heading = normalized.lstrip("#").strip()
        if heading.lower() in {"memories", "user memories"}:
            return ""
        normalized = heading
    if normalized.startswith(("- ", "* ", "+ ")):
        normalized = normalized[2:].strip()
    normalized = _MARKDOWN_LINK_RE.sub(r"\1", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    if normalized.lower() in {"memories", "user memories"}:
        return ""
    return normalized


def _summarize_title(content: Any, *, max_chars: int = 28) -> str:
    raw = str(content or "")
    for line in raw.splitlines():
        candidate = _clean_title_line(line)
        if candidate:
            return candidate[: max_chars - 1] + "…" if len(candidate) > max_chars else candidate
    return ""


class TranscriptWriter:
    """追加写入 transcript 文件。"""

    def __init__(
        self,
        file_path: Path,
        *,
        session_meta: dict[str, Any] | None = None,
    ) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        should_write_meta = session_meta is not None and (
            not file_path.exists() or file_path.stat().st_size == 0
        )
        self.file_path = file_path
        self._file = file_path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        if should_write_meta:
            self._write_record({"type": _META_RECORD_TYPE, "session": dict(session_meta or {})})

    def _write_record(self, record: dict[str, Any]) -> None:
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    def write_message(self, msg: BaseMessage) -> None:
        with self._lock:
            self._write_record(_message_to_record(msg))

    def close(self) -> None:
        with self._lock:
            self._file.close()

    def read_all_messages(self) -> list[BaseMessage]:
        """Read the current transcript content for session continuation."""
        with self._lock:
            self._file.flush()
        return TranscriptReader(self.file_path).read_all()


class TranscriptReader:
    """读取 transcript 文件，还原为消息列表。"""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def _iter_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with self.file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        return records

    def read_all(self) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        for record in self._iter_records():
            if record.get("type") == _META_RECORD_TYPE:
                continue
            messages.append(_record_to_message(record))
        return messages

    def read_info(self) -> dict[str, Any]:
        records = self._iter_records()
        session_meta: dict[str, Any] = {}
        messages: list[BaseMessage] = []
        for record in records:
            if record.get("type") == _META_RECORD_TYPE:
                session_meta = dict(record.get("session", {}) or {})
                continue
            messages.append(_record_to_message(record))
        title = ""
        for msg in messages:
            if isinstance(msg, HumanMessage):
                title = _summarize_title(msg.content)
                break
        return {
            "message_count": len(messages),
            "title": title,
            "cwd": str(session_meta.get("cwd", "")),
        }
