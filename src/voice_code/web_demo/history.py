"""Transcript JSONL → 前端会话回放模型（ConversationTurn[]）的映射。

前端 conversation reducer 以"回合"为单位渲染（userText / assistantText /
tools / finishReason），历史回放直接产出同构数据，前端 mapper 只做透传。
所有字符串经过沙盒绝对路径脱敏。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from voice_code.session import TranscriptReader
from voice_code.web_demo.handlers import _replace_sandbox_text

_IMAGE_PLACEHOLDER = "[图片]"


def transcript_to_turns(
    transcript_path: Path,
    *,
    sandbox_path: Path,
    max_tool_chars: int = 4_000,
) -> list[dict[str, Any]]:
    """把持久化 transcript 映射为前端 ConversationTurn 列表。"""
    if not transcript_path.exists():
        return []
    try:
        messages = TranscriptReader(transcript_path).read_all()
    except (OSError, ValueError):
        return []

    sandbox_text = str(sandbox_path)
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    tools_by_id: dict[str, dict[str, Any]] = {}

    def new_turn(user_text: str = "") -> dict[str, Any]:
        turn: dict[str, Any] = {
            "id": len(turns) + 1,
            "userText": user_text,
            "assistantText": "",
            "reasoningText": "",
            "status": "completed",
            "finishReason": "completed",
            "tools": [],
        }
        turns.append(turn)
        return turn

    for message in messages:
        if isinstance(message, HumanMessage):
            current = new_turn(_message_text(message))
            tools_by_id = {}
            continue
        if isinstance(message, AIMessage):
            if current is None:
                current = new_turn()
            current["assistantText"] += _message_text(message)
            reasoning = message.additional_kwargs.get("reasoning_content", "")
            if isinstance(reasoning, str) and reasoning:
                current["reasoningText"] += reasoning
            for tool_call in message.tool_calls or []:
                tool = {
                    "id": str(tool_call.get("id", "")),
                    "name": str(tool_call.get("name", "")),
                    "args": tool_call.get("args", {}),
                    "result": "",
                    "resultPreview": "",
                    "status": "running",
                }
                current["tools"].append(tool)
                tools_by_id[tool["id"]] = tool
            continue
        if isinstance(message, ToolMessage):
            if current is None:
                current = new_turn()
            content = str(message.content)
            is_error = content.startswith("<tool_use_error>")
            tool = tools_by_id.get(str(message.tool_call_id))
            if tool is None:
                # 孤立的 tool 结果（transcript 截断等边界情况）也要看得见
                tool = {
                    "id": str(message.tool_call_id),
                    "name": str(message.name or "tool"),
                    "args": {},
                    "result": "",
                    "resultPreview": "",
                    "status": "running",
                }
                current["tools"].append(tool)
            tool["result"] = content[:max_tool_chars]
            tool["resultPreview"] = " ".join(content.split())[:300]
            tool["status"] = "error" if is_error else "completed"

    # 回合中断留下的 running 工具统一收口为 completed，前端不再转圈
    for turn in turns:
        for tool in turn["tools"]:
            if tool["status"] == "running":
                tool["status"] = "completed"

    return [_sanitize_turn(turn, sandbox_text) for turn in turns]


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif item.get("type") in {"image", "image_url"}:
                parts.append(_IMAGE_PLACEHOLDER)
        else:
            parts.append(str(item))
    return "".join(parts)


def _sanitize_turn(turn: dict[str, Any], sandbox_text: str) -> dict[str, Any]:
    turn = dict(turn)
    for key in ("userText", "assistantText", "reasoningText"):
        turn[key] = _replace_sandbox_text(str(turn[key]), sandbox_text)
    sanitized_tools = []
    for tool in turn["tools"]:
        tool = dict(tool)
        tool["result"] = _replace_sandbox_text(str(tool["result"]), sandbox_text)
        tool["resultPreview"] = _replace_sandbox_text(str(tool["resultPreview"]), sandbox_text)
        sanitized_tools.append(tool)
    turn["tools"] = sanitized_tools
    return turn
