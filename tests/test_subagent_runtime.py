from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from voice_code.agent.types import AgentEvent, EventType
from voice_code.permissions import PermissionContext
from voice_code.subagents.registry import TaskRegistry
from voice_code.subagents.runtime import SubagentRuntime, SubagentRuntimeRequest
from voice_code.subagents.types import TaskStatus


async def _successful_loop(**_: Any) -> AsyncGenerator[AgentEvent, None]:
    yield AgentEvent(type=EventType.TEXT, turn=1, content="working")
    yield AgentEvent(type=EventType.TOOL_RESULT, turn=1, tool_name="read", tool_result="ok")
    yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")


async def _failing_loop(**_: Any) -> AsyncGenerator[AgentEvent, None]:
    yield AgentEvent(type=EventType.ERROR, turn=1, content="boom", status="generic_error")
    yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="error")


@pytest.mark.asyncio
async def test_runtime_completes_and_writes_transcript():
    registry = TaskRegistry()
    with TemporaryDirectory() as tmp:
        runtime = SubagentRuntime(
            registry=registry,
            transcript_root=Path(tmp),
            agent_loop_fn=_successful_loop,
        )

        result = await runtime.run(
            SubagentRuntimeRequest(
                task_id="task-1",
                session_id="session-1",
                parent_session_id="session-1",
                parent_task_id=None,
                agent_type="researcher",
                description="Research",
                prompt="Find facts",
                system_prompt="You are a researcher",
                tools=[],
                model=object(),
                fallback_model=None,
                permission_context=PermissionContext(),
            )
        )

        task = registry.get_task("task-1")
        assert task is not None
        assert task.status == TaskStatus.COMPLETED
        assert result.summary == "completed"
        assert Path(result.transcript_path).exists()


@pytest.mark.asyncio
async def test_runtime_marks_failure():
    registry = TaskRegistry()
    with TemporaryDirectory() as tmp:
        runtime = SubagentRuntime(
            registry=registry,
            transcript_root=Path(tmp),
            agent_loop_fn=_failing_loop,
        )

        result = await runtime.run(
            SubagentRuntimeRequest(
                task_id="task-2",
                session_id="session-1",
                parent_session_id="session-1",
                parent_task_id=None,
                agent_type="reviewer",
                description="Review",
                prompt="Review code",
                system_prompt="You are a reviewer",
                tools=[],
                model=object(),
                fallback_model=None,
                permission_context=PermissionContext(),
            )
        )

        task = registry.get_task("task-2")
        assert task is not None
        assert task.status == TaskStatus.FAILED
        assert result.summary == "failed"
