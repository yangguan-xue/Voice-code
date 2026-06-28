"""流式工具执行器."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from langchain_core.tools import BaseTool

from voice_code.agent.types import AgentEvent, EventType
from voice_code.tools import find_tool_by_name

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RESULT_CHARS = 50_000
_TRUNCATION_NOTICE = (
    "\n\n... [TRUNCATED: {original} total chars, {shown} shown. "
    "Use offset/limit to read more.]"
)

_ERROR_TOOL_NOT_FOUND = (
    "<tool_use_error>Error: No such tool available: {name}</tool_use_error>"
)
_ERROR_TOOL_EXEC = (
    "<tool_use_error>Error calling tool ({name}): {error}</tool_use_error>"
)
_ERROR_ABORTED = (
    "<tool_use_error>Error: Tool execution aborted: {reason}</tool_use_error>"
)


class ToolStatus(Enum):
    QUEUED = auto()
    EXECUTING = auto()
    YIELDED = auto()


class AbortReason(Enum):
    USER_INTERRUPTED = auto()
    STREAMING_FALLBACK = auto()


@dataclass
class TrackedTool:
    id: str
    name: str
    args: dict[str, Any]
    tool: BaseTool | None
    turn: int
    is_concurrent_safe: bool
    status: ToolStatus = ToolStatus.QUEUED
    task: asyncio.Task[None] | None = None


class StreamingToolExecutor:
    """按完成顺序产出工具结果，并处理并发/独占调度."""

    def __init__(
        self,
        tool_definitions: list[BaseTool],
        turn: int,
    ) -> None:
        self._tool_definitions = tool_definitions
        self._turn = turn
        self._tools: list[TrackedTool] = []
        self._completed_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._yielded_count = 0
        self._aborted = False
        self._abort_reason: AbortReason | None = None

    def add_all(self, tool_calls: list[dict[str, Any]]) -> None:
        """批量添加工具调用到执行队列并触发调度."""
        for tc in tool_calls:
            self._add_one(tc)
        self._process_queue()

    def abort(self, reason: AbortReason) -> None:
        """中断执行中的与待执行的工具."""
        if self._aborted:
            return
        self._aborted = True
        self._abort_reason = reason

        for tracked in self._tools:
            if tracked.status == ToolStatus.EXECUTING and tracked.task is not None:
                tracked.task.cancel()

        self._emit_abort_for_queued()

    def get_completed_results(self) -> list[AgentEvent]:
        """非阻塞获取已完成结果."""
        results: list[AgentEvent] = []
        while True:
            try:
                results.append(self._completed_queue.get_nowait())
                self._yielded_count += 1
            except asyncio.QueueEmpty:
                break
        return results

    async def get_remaining_results(self) -> list[AgentEvent]:
        """等待全部工具结束，按完成顺序返回结果."""
        results: list[AgentEvent] = []
        remaining = len(self._tools) - self._yielded_count
        while len(results) < remaining:
            results.append(await self._completed_queue.get())
            self._yielded_count += 1
        return results

    def _add_one(self, tc: dict[str, Any]) -> None:
        tool_name = str(tc.get("name", ""))
        tool = find_tool_by_name(tool_name, self._tool_definitions)
        metadata = tool.metadata if tool and isinstance(tool.metadata, dict) else {}
        self._tools.append(
            TrackedTool(
                id=str(tc.get("id", "")),
                name=tool_name,
                args=tc.get("args", {}),
                tool=tool,
                turn=self._turn,
                is_concurrent_safe=bool(metadata.get("is_concurrency_safe", False)),
            )
        )

    def _has_executing_exclusive(self) -> bool:
        return any(
            tracked.status == ToolStatus.EXECUTING
            and not tracked.is_concurrent_safe
            for tracked in self._tools
        )

    def _has_executing_any(self) -> bool:
        return any(tracked.status == ToolStatus.EXECUTING for tracked in self._tools)

    def _process_queue(self) -> None:
        if self._aborted:
            self._emit_abort_for_queued()
            return

        if self._has_executing_exclusive():
            return

        queued = [tracked for tracked in self._tools if tracked.status == ToolStatus.QUEUED]
        if not queued:
            return

        if self._has_executing_any():
            for tracked in queued:
                if tracked.is_concurrent_safe:
                    self._start_tool(tracked)
            return

        concurrent_tools = [tracked for tracked in queued if tracked.is_concurrent_safe]
        if concurrent_tools:
            for tracked in concurrent_tools:
                self._start_tool(tracked)
            return

        self._start_tool(queued[0])

    def _start_tool(self, tracked: TrackedTool) -> None:
        tracked.status = ToolStatus.EXECUTING
        tracked.task = asyncio.create_task(self._execute_tool(tracked))

    def _get_max_result_chars(self, tool: BaseTool | None) -> int | None:
        if tool is None or not isinstance(tool.metadata, dict):
            return _DEFAULT_MAX_RESULT_CHARS

        value = tool.metadata.get("max_result_chars", _DEFAULT_MAX_RESULT_CHARS)
        if value is None:
            return None
        if value == float("inf"):
            return None
        return int(value)

    def _apply_result_budget(self, tool: BaseTool | None, content: str) -> str:
        if content.startswith("<tool_use_error>"):
            return content

        max_chars = self._get_max_result_chars(tool)
        if max_chars is None or len(content) <= max_chars:
            return content

        return content[:max_chars] + _TRUNCATION_NOTICE.format(
            original=len(content),
            shown=max_chars,
        )

    async def _execute_tool(self, tracked: TrackedTool) -> None:
        try:
            if tracked.tool is None:
                content = _ERROR_TOOL_NOT_FOUND.format(name=tracked.name)
                event_type = EventType.ERROR
            else:
                result = await asyncio.to_thread(tracked.tool.invoke, tracked.args)
                content = self._apply_result_budget(tracked.tool, str(result))
                event_type = EventType.TOOL_RESULT
        except asyncio.CancelledError:
            content = _ERROR_ABORTED.format(
                reason=(self._abort_reason.name.lower() if self._abort_reason else "cancelled")
            )
            event_type = EventType.ERROR
        except Exception as exc:
            logger.exception("Tool execution failed: %s", tracked.name)
            content = _ERROR_TOOL_EXEC.format(name=tracked.name, error=exc)
            event_type = EventType.ERROR
        finally:
            tracked.status = ToolStatus.YIELDED
            await self._completed_queue.put(
                AgentEvent(
                    type=event_type,
                    turn=tracked.turn,
                    content=content,
                    status="" if event_type == EventType.TOOL_RESULT else "generic_error",
                    tool_name=tracked.name,
                    tool_call_id=tracked.id,
                    tool_args=tracked.args,
                    tool_result=content if event_type == EventType.TOOL_RESULT else "",
                )
            )
            self._process_queue()

    def _emit_abort_for_queued(self) -> None:
        reason = self._abort_reason.name.lower() if self._abort_reason else "aborted"
        for tracked in self._tools:
            if tracked.status != ToolStatus.QUEUED:
                continue
            tracked.status = ToolStatus.YIELDED
            self._completed_queue.put_nowait(
                AgentEvent(
                    type=EventType.ERROR,
                    turn=tracked.turn,
                    content=_ERROR_ABORTED.format(reason=reason),
                    status="generic_error",
                    tool_name=tracked.name,
                    tool_call_id=tracked.id,
                    tool_args=tracked.args,
                )
            )
