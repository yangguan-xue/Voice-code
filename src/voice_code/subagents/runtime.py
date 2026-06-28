"""子 agent 运行时包装层。"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from voice_code.agent.types import AgentEvent, EventType
from voice_code.permissions import PermissionContext
from voice_code.session.transcript import TranscriptWriter
from voice_code.subagents.registry import TaskRegistry
from voice_code.subagents.transcripts import get_subagent_transcript_path
from voice_code.subagents.types import AgentTask, TaskProgress, TaskStatus, TaskSummary

AgentLoopFn = Callable[..., AsyncGenerator[AgentEvent, None]]


@dataclass(slots=True)
class SubagentRuntimeRequest:
    task_id: str
    session_id: str
    parent_session_id: str | None
    parent_task_id: str | None
    agent_type: str
    description: str
    prompt: str
    system_prompt: str
    tools: list[Any]
    model: Any
    fallback_model: Any | None
    permission_context: PermissionContext
    max_turns: int = 30


@dataclass(slots=True)
class SubagentRunResult:
    task_id: str
    status: TaskStatus
    summary: str
    transcript_path: str


class SubagentRuntime:
    """将单 agent loop 包装成可管理的子 agent task。"""

    def __init__(
        self,
        *,
        registry: TaskRegistry,
        transcript_root: Path,
        agent_loop_fn: AgentLoopFn | None = None,
    ) -> None:
        if agent_loop_fn is None:
            from voice_code.agent.loop import agent_loop

            agent_loop_fn = agent_loop
        self._registry = registry
        self._transcript_root = transcript_root
        self._agent_loop_fn = agent_loop_fn

    async def run(self, request: SubagentRuntimeRequest) -> SubagentRunResult:
        transcript_path = get_subagent_transcript_path(
            self._transcript_root, request.session_id, request.task_id
        )
        writer = TranscriptWriter(transcript_path)

        task = AgentTask(
            task_id=request.task_id,
            session_id=request.session_id,
            parent_task_id=request.parent_task_id,
            parent_session_id=request.parent_session_id,
            agent_type=request.agent_type,
            description=request.description,
            prompt=request.prompt,
            status=TaskStatus.PENDING,
            model_name=str(getattr(request.model, "model_name", request.model)),
            transcript_path=str(transcript_path),
            created_at=time.time(),
        )
        self._registry.create_task(task)
        self._registry.update_status(request.task_id, TaskStatus.RUNNING)

        text_chunks: list[str] = []
        error_message = ""
        tool_use_count = 0
        finish_reason = "error"
        started_at = time.time()

        try:
            async for event in self._agent_loop_fn(
                user_input=request.prompt,
                tools=request.tools,
                system_prompt=request.system_prompt,
                model=request.model,
                max_turns=request.max_turns,
                permission_context=request.permission_context,
                transcript_writer=writer,
                fallback_model=request.fallback_model,
                runtime_session_id=request.session_id,
            ):
                if event.type == EventType.TEXT and event.content:
                    text_chunks.append(event.content)
                    self._registry.update_progress(
                        request.task_id,
                        TaskProgress(
                            tool_use_count=tool_use_count,
                            token_count=0,
                            last_activity="streaming_text",
                            summary="streaming",
                        ),
                    )
                elif event.type == EventType.TOOL_CALL:
                    tool_use_count += 1
                    self._registry.update_progress(
                        request.task_id,
                        TaskProgress(
                            tool_use_count=tool_use_count,
                            token_count=0,
                            last_activity=event.tool_name,
                            summary=f"tool:{event.tool_name}",
                        ),
                    )
                elif event.type == EventType.ERROR and event.content:
                    error_message = event.content
                elif event.type == EventType.FINISH:
                    finish_reason = event.finish_reason or "error"
        finally:
            if text_chunks:
                writer.write_message(AIMessage(content="".join(text_chunks)))
            writer.close()

        duration_ms = int((time.time() - started_at) * 1000)
        if finish_reason == "completed":
            self._registry.complete_task(
                request.task_id,
                TaskSummary(
                    summary="completed",
                    transcript_path=str(transcript_path),
                    tool_uses=tool_use_count,
                    duration_ms=duration_ms,
                ),
            )
            return SubagentRunResult(
                task_id=request.task_id,
                status=TaskStatus.COMPLETED,
                summary="completed",
                transcript_path=str(transcript_path),
            )

        self._registry.fail_task(request.task_id, error=error_message or "subagent failed")
        return SubagentRunResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            summary="failed",
            transcript_path=str(transcript_path),
        )
