from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from voice_code.permissions import PermissionContext
from voice_code.subagents.planner import AgentToolRequest
from voice_code.subagents.service import (
    RuntimeInvocationContext,
    SubagentService,
    TaskNotification,
    activate_runtime_context,
)
from voice_code.subagents.types import AgentTask, TaskStatus
from voice_code.telemetry.context import bind_telemetry_context
from voice_code.tools.agent import agent
from voice_code.tools.task_get import task_get
from voice_code.tools.task_list import task_list


@pytest.mark.asyncio
async def test_task_tools_read_from_current_service(tmp_path: Path):
    event_loop = asyncio.get_running_loop()
    service = SubagentService(
        session_id="session-tools",
        event_loop=event_loop,
        transcript_root=tmp_path,
    )
    service.registry.create_task(
        AgentTask(
            task_id="task-1",
            session_id="session-tools",
            parent_task_id=None,
            parent_session_id=None,
            agent_type="researcher",
            description="Research task",
            prompt="Find facts",
            status=TaskStatus.PENDING,
            model_name="test-model",
            transcript_path=str(tmp_path / "task-1.jsonl"),
            created_at=1.0,
        )
    )
    context = RuntimeInvocationContext(
        session_id="session-tools",
        system_prompt="system",
        model=object(),
        fallback_model=None,
        permission_context=PermissionContext(),
        tools=[],
        event_loop=event_loop,
        service=service,
    )

    with activate_runtime_context(context):
        listing = await asyncio.to_thread(task_list.invoke, {})
        details = await asyncio.to_thread(task_get.invoke, {"task_id": "task-1"})

    assert "task-1" in listing
    assert "Research task" in listing
    assert "agent_type: researcher" in details


@pytest.mark.asyncio
async def test_agent_tool_sync_returns_structured_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    event_loop = asyncio.get_running_loop()
    service = SubagentService(
        session_id="session-agent",
        event_loop=event_loop,
        transcript_root=tmp_path,
    )

    async def fake_run_single(request):
        from voice_code.subagents.runtime import SubagentRunResult

        return SubagentRunResult(
            task_id=request.task_id,
            status=TaskStatus.COMPLETED,
            summary="completed",
            transcript_path=str(tmp_path / f"{request.task_id}.jsonl"),
        )

    monkeypatch.setattr(service, "_run_single", fake_run_single)
    context = RuntimeInvocationContext(
        session_id="session-agent",
        system_prompt="system",
        model=object(),
        fallback_model=None,
        permission_context=PermissionContext(),
        tools=[],
        event_loop=event_loop,
        service=service,
    )

    with activate_runtime_context(context):
        result = await asyncio.to_thread(
            agent.invoke,
            {
                "description": "Review code",
                "prompt": "Review the patch",
                "subagent_type": "reviewer",
                "run_in_background": False,
            },
        )

    assert "status: completed" in result
    assert "task_id:" in result


@pytest.mark.asyncio
async def test_background_subagent_uses_supervisor_metadata(tmp_path: Path, monkeypatch):
    event_loop = asyncio.get_running_loop()
    service = SubagentService(
        session_id="session-background",
        event_loop=event_loop,
        transcript_root=tmp_path,
    )
    started = asyncio.Event()
    unblock = asyncio.Event()

    async def fake_run_single(request):
        from voice_code.subagents.runtime import SubagentRunResult

        started.set()
        await unblock.wait()
        return SubagentRunResult(
            task_id=request.task_id,
            status=TaskStatus.COMPLETED,
            summary="completed",
            transcript_path=str(tmp_path / f"{request.task_id}.jsonl"),
        )

    monkeypatch.setattr(service, "_run_single", fake_run_single)
    context = RuntimeInvocationContext(
        session_id="session-background",
        system_prompt="system",
        model=object(),
        fallback_model=None,
        permission_context=PermissionContext(),
        tools=[],
        event_loop=event_loop,
        service=service,
    )

    with bind_telemetry_context(correlation_id="corr-subagent"):
        task_ids = await service._launch_background(
            context,
            AgentToolRequest(
                description="Review code",
                prompt="Review the patch",
                subagent_type="reviewer",
                run_in_background=True,
            ),
            1,
        )
    await started.wait()

    record = service.task_supervisor.get_task(task_ids[0])
    assert record is not None
    assert record.owner == "subagent"
    assert record.correlation_id == "corr-subagent"

    unblock.set()
    await service.task_supervisor.shutdown()


@pytest.mark.asyncio
async def test_subagent_service_exports_and_restores_runtime_state(tmp_path: Path):
    event_loop = asyncio.get_running_loop()
    service = SubagentService(
        session_id="session-export",
        event_loop=event_loop,
        transcript_root=tmp_path,
    )
    service.registry.create_task(
        AgentTask(
            task_id="task-1",
            session_id="session-export",
            parent_task_id=None,
            parent_session_id=None,
            agent_type="researcher",
            description="Research task",
            prompt="Find facts",
            status=TaskStatus.RUNNING,
            model_name="test-model",
            transcript_path=str(tmp_path / "task-1.jsonl"),
            created_at=1.0,
        )
    )
    service._notifications.append(  # type: ignore[attr-defined]
        TaskNotification(
            task_id="task-1",
            session_id="session-export",
            agent_type="researcher",
            status=TaskStatus.COMPLETED,
            summary="done",
            transcript_path=str(tmp_path / "task-1.jsonl"),
        )
    )

    task_state = service.export_task_state()
    notifications = service.export_notifications_state()

    restored = SubagentService(
        session_id="session-export",
        event_loop=event_loop,
        transcript_root=tmp_path,
    )
    restored.restore_runtime_state(task_state=task_state, notifications=notifications)

    tasks = restored.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].task_id == "task-1"
    assert tasks[0].status == TaskStatus.INTERRUPTED
    assert tasks[0].error == "interrupted before session resume"
    drained = restored.drain_notifications_as_messages()
    assert len(drained) == 1
    assert "task_id: task-1" in str(drained[0].content)
