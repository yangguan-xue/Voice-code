"""子 agent 服务与当前运行上下文。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from voice_code.llm.models import init_model
from voice_code.permissions import PermissionContext
from voice_code.session import get_transcript_dir, make_session_id
from voice_code.session.transcript import TranscriptReader
from voice_code.subagents.definitions import (
    AgentDefinition,
    filter_tools_for_definition,
    get_agent_definition,
)
from voice_code.subagents.fork import build_fork_system_prompt, validate_fork_prompt
from voice_code.subagents.planner import AgentToolRequest, SpawnMode, plan_spawn
from voice_code.subagents.registry import TaskRegistry
from voice_code.subagents.runtime import (
    SubagentRunResult,
    SubagentRuntime,
    SubagentRuntimeRequest,
)
from voice_code.subagents.types import AgentTask, TaskStatus


@dataclass(slots=True)
class TaskNotification:
    task_id: str
    session_id: str
    agent_type: str
    status: TaskStatus
    summary: str
    transcript_path: str

    def to_message(self) -> HumanMessage:
        return HumanMessage(
            content=(
                "<task_notification>\n"
                f"task_id: {self.task_id}\n"
                f"agent_type: {self.agent_type}\n"
                f"status: {self.status}\n"
                f"summary: {self.summary}\n"
                f"transcript_path: {self.transcript_path}\n"
                "</task_notification>"
            )
        )


@dataclass(slots=True)
class RuntimeInvocationContext:
    session_id: str
    system_prompt: str
    model: Any
    fallback_model: Any | None
    permission_context: PermissionContext
    tools: list[BaseTool]
    event_loop: asyncio.AbstractEventLoop
    service: SubagentService


_CURRENT_CONTEXT: ContextVar[RuntimeInvocationContext | None] = ContextVar(
    "subagent_runtime_context",
    default=None,
)
_SERVICES: dict[str, SubagentService] = {}


@contextmanager
def activate_runtime_context(context: RuntimeInvocationContext) -> Iterator[None]:
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_CONTEXT.reset(token)


def get_current_runtime_context() -> RuntimeInvocationContext:
    context = _CURRENT_CONTEXT.get()
    if context is None:
        raise RuntimeError("Subagent runtime context is not available")
    return context


def get_or_create_service(
    session_id: str,
    *,
    event_loop: asyncio.AbstractEventLoop,
    transcript_root: Path | None = None,
) -> SubagentService:
    service = _SERVICES.get(session_id)
    if service is None:
        service = SubagentService(
            session_id=session_id,
            event_loop=event_loop,
            transcript_root=transcript_root or get_transcript_dir(),
        )
        _SERVICES[session_id] = service
    else:
        service.bind_event_loop(event_loop)
    return service


class SubagentService:
    """负责调度子 agent 任务与通知。"""

    def __init__(
        self,
        *,
        session_id: str,
        event_loop: asyncio.AbstractEventLoop,
        transcript_root: Path,
    ) -> None:
        self._session_id = session_id
        self._event_loop = event_loop
        self._registry = TaskRegistry()
        self._runtime = SubagentRuntime(
            registry=self._registry,
            transcript_root=transcript_root,
        )
        self._lock = threading.RLock()
        self._background_tasks: dict[str, asyncio.Task[None]] = {}
        self._notifications: list[TaskNotification] = []

    @property
    def registry(self) -> TaskRegistry:
        return self._registry

    def bind_event_loop(self, event_loop: asyncio.AbstractEventLoop) -> None:
        self._event_loop = event_loop

    def invoke_agent_tool(self, request: AgentToolRequest) -> str:
        context = get_current_runtime_context()
        plan = plan_spawn(request)
        if plan.mode == SpawnMode.FRESH_SYNC:
            result = asyncio.run_coroutine_threadsafe(
                self._run_single(self._build_runtime_request(context, request)),
                self._event_loop,
            ).result()
            return self._format_sync_result(result)

        task_ids = asyncio.run_coroutine_threadsafe(
            self._launch_background(context, request, plan.count),
            self._event_loop,
        ).result()
        if len(task_ids) == 1:
            return f"Agent launched in background: {task_ids[0]}"
        return f"Agents launched in background: {', '.join(task_ids)}"

    def list_tasks_text(self) -> str:
        tasks = self.list_tasks()
        if not tasks:
            return "No subagent tasks."
        return "\n".join(
            f"{task.task_id} [{task.status}] {task.agent_type} - {task.description}"
            for task in tasks
        )

    def list_tasks(self) -> list[AgentTask]:
        return self._registry.list_tasks(session_id=self._session_id)

    def get_task_text(self, task_id: str) -> str:
        task = self._registry.get_task(task_id)
        if task is None:
            return f"Task not found: {task_id}"
        return "\n".join(
            [
                f"task_id: {task.task_id}",
                f"status: {task.status}",
                f"agent_type: {task.agent_type}",
                f"description: {task.description}",
                f"transcript_path: {task.transcript_path}",
                f"summary: {task.result_summary or ''}",
                f"error: {task.error or ''}",
            ]
        )

    def get_task_transcript_path(self, task_id: str) -> str | None:
        task = self._registry.get_task(task_id)
        if task is None:
            return None
        return task.transcript_path

    def get_task_transcript_text(self, task_id: str, *, max_chars: int | None = 5000) -> str:
        task = self._registry.get_task(task_id)
        if task is None:
            return f"Task not found: {task_id}"
        path = Path(task.transcript_path)
        if not path.exists():
            return f"Transcript not found: {task.transcript_path}"
        messages = TranscriptReader(path).read_all()
        lines: list[str] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                lines.append("[system]")
                lines.append(str(message.content))
            elif isinstance(message, HumanMessage):
                lines.append("[user]")
                lines.append(str(message.content))
            elif isinstance(message, AIMessage):
                lines.append("[assistant]")
                lines.append(str(message.content))
            elif isinstance(message, ToolMessage):
                lines.append(f"[tool:{message.name or 'unknown'}]")
                lines.append(str(message.content))
            lines.append("")
        text = "\n".join(lines).strip()
        if max_chars is None or len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n\n... [truncated]"

    def stop_task(self, task_id: str) -> str:
        future = asyncio.run_coroutine_threadsafe(self._stop_task(task_id), self._event_loop)
        return future.result()

    def stop_all_tasks(self) -> str:
        future = asyncio.run_coroutine_threadsafe(self._stop_all_tasks(), self._event_loop)
        return future.result()

    def drain_notifications_as_messages(self) -> list[HumanMessage]:
        with self._lock:
            notifications = list(self._notifications)
            self._notifications.clear()
        return [notification.to_message() for notification in notifications]

    async def _launch_background(
        self,
        context: RuntimeInvocationContext,
        request: AgentToolRequest,
        count: int,
    ) -> list[str]:
        task_ids: list[str] = []
        for _ in range(count):
            runtime_request = self._build_runtime_request(context, request)
            task_ids.append(runtime_request.task_id)
            task = asyncio.create_task(self._run_single(runtime_request))
            self._background_tasks[runtime_request.task_id] = task
            task.add_done_callback(
                lambda _, task_id=runtime_request.task_id: self._background_tasks.pop(task_id, None)
            )
        return task_ids

    async def _run_single(self, request: SubagentRuntimeRequest) -> SubagentRunResult:
        result = await self._runtime.run(request)
        self._record_notification(result, request.agent_type)
        return result

    async def _stop_task(self, task_id: str) -> str:
        task = self._background_tasks.get(task_id)
        if task is None:
            if self._registry.get_task(task_id) is None:
                return f"Task not found: {task_id}"
            return f"Task is not running: {task_id}"
        self._registry.request_stop(task_id)
        task.cancel()
        self._registry.cancel_task(task_id, error="cancelled by user")
        return f"Stopped task: {task_id}"

    async def _stop_all_tasks(self) -> str:
        if not self._background_tasks:
            return "No running tasks."
        task_ids = list(self._background_tasks)
        for task_id in task_ids:
            await self._stop_task(task_id)
        return f"Stopped tasks: {', '.join(task_ids)}"

    def _build_runtime_request(
        self,
        context: RuntimeInvocationContext,
        request: AgentToolRequest,
    ) -> SubagentRuntimeRequest:
        task_id = make_session_id()
        if request.subagent_type is None:
            validate_fork_prompt(request.prompt)
            system_prompt = build_fork_system_prompt(context.system_prompt)
            agent_type = "fork"
            tools = list(context.tools)
            model = context.model
            max_turns = 30
        else:
            definition = get_agent_definition(request.subagent_type)
            system_prompt = definition.system_prompt
            agent_type = definition.agent_type
            tools = filter_tools_for_definition(context.tools, definition)
            model = self._resolve_model(context, request, definition)
            max_turns = definition.max_turns or 30

        permission_context = PermissionContext(
            mode=context.permission_context.mode,
            session_whitelist=context.permission_context.session_whitelist,
            output_format=context.permission_context.output_format,
            approver=context.permission_context.approver,
            session_id=context.session_id,
            task_id=task_id,
            agent_type=agent_type,
            parent_session_id=context.session_id,
        )

        return SubagentRuntimeRequest(
            task_id=task_id,
            session_id=context.session_id,
            parent_session_id=context.session_id,
            parent_task_id=None,
            agent_type=agent_type,
            description=request.description,
            prompt=request.prompt,
            system_prompt=system_prompt,
            tools=tools,
            model=model,
            fallback_model=context.fallback_model,
            permission_context=permission_context,
            max_turns=max_turns,
        )

    def _resolve_model(
        self,
        context: RuntimeInvocationContext,
        request: AgentToolRequest,
        definition: AgentDefinition,
    ) -> Any:
        model_name = request.model or definition.model
        if model_name:
            return init_model(model_name=model_name)
        return context.model

    def _record_notification(self, result: SubagentRunResult, agent_type: str) -> None:
        with self._lock:
            self._notifications.append(
                TaskNotification(
                    task_id=result.task_id,
                    session_id=self._session_id,
                    agent_type=agent_type,
                    status=result.status,
                    summary=result.summary,
                    transcript_path=result.transcript_path,
                )
            )

    def _format_sync_result(self, result: SubagentRunResult) -> str:
        return "\n".join(
            [
                f"status: {result.status}",
                f"task_id: {result.task_id}",
                f"summary: {result.summary}",
                f"transcript_path: {result.transcript_path}",
            ]
        )
