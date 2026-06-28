"""Transcript JSONL reader/writer."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


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


class TranscriptWriter:
    """追加写入 transcript 文件。"""

    def __init__(self, file_path: Path) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path = file_path
        self._file = file_path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def write_message(self, msg: BaseMessage) -> None:
        with self._lock:
            self._file.write(json.dumps(_message_to_record(msg), ensure_ascii=False) + "\n")
            self._file.flush()

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

    def read_all(self) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        with self.file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                messages.append(_record_to_message(json.loads(line)))
        return messages

    def read_info(self) -> dict[str, Any]:
        messages = self.read_all()
        title = ""
        for msg in messages:
            if isinstance(msg, HumanMessage):
                title = str(msg.content)
                break
        return {
            "message_count": len(messages),
            "title": title,
        }
