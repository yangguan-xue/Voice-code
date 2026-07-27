from __future__ import annotations

import asyncio

import pytest

from voice_code.task_supervisor import TaskRejectedError, TaskSupervisor, TaskSupervisorConfig
from voice_code.telemetry.context import bind_telemetry_context


@pytest.mark.asyncio
async def test_task_exception_is_consumed_without_loop_warning() -> None:
    loop = asyncio.get_running_loop()
    errors: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _, context: errors.append(context))
    supervisor = TaskSupervisor(TaskSupervisorConfig(max_concurrent_tasks=1))

    async def fail() -> None:
        raise RuntimeError("private failure")

    with bind_telemetry_context(correlation_id="corr-test"):
        supervisor.start_soon(fail(), owner="test", task_id="task-fail", name="failure")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    record = supervisor.get_task("task-fail")
    assert record is not None
    assert record.status == "failed"
    assert record.exception_type == "RuntimeError"
    assert not errors


@pytest.mark.asyncio
async def test_shutdown_timeout_escalates_and_consumes_cancelled_task() -> None:
    supervisor = TaskSupervisor(TaskSupervisorConfig(max_concurrent_tasks=1, shutdown_timeout=0.01))
    cancelled = asyncio.Event()

    async def ignore_cancel() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            await asyncio.sleep(60)

    with bind_telemetry_context(correlation_id="corr-test"):
        supervisor.start_soon(ignore_cancel(), owner="test", task_id="task-stuck", name="stuck")
    await asyncio.sleep(0)
    report = await supervisor.shutdown()

    assert cancelled.is_set()
    assert report.timed_out == ["task-stuck"]
    record = supervisor.get_task("task-stuck")
    assert record is not None
    assert record.status == "shutdown_timeout"


@pytest.mark.asyncio
async def test_concurrency_limit_rejects_new_tasks() -> None:
    supervisor = TaskSupervisor(TaskSupervisorConfig(max_concurrent_tasks=1))
    blocker = asyncio.Event()

    async def wait_forever() -> None:
        await blocker.wait()

    with bind_telemetry_context(correlation_id="corr-test"):
        supervisor.start_soon(wait_forever(), owner="test", task_id="task-1", name="first")

        with pytest.raises(TaskRejectedError):
            supervisor.start_soon(wait_forever(), owner="test", task_id="task-2", name="second")

    blocker.set()
    await supervisor.shutdown()
    rejected = supervisor.get_task("task-2")
    assert rejected is not None
    assert rejected.status == "rejected"


@pytest.mark.asyncio
async def test_owner_and_correlation_id_are_required() -> None:
    supervisor = TaskSupervisor(TaskSupervisorConfig(max_concurrent_tasks=1))

    async def noop() -> None:
        return None

    with pytest.raises(ValueError):
        supervisor.start_soon(noop(), owner="", task_id="missing-owner", name="noop")

    with pytest.raises(ValueError):
        supervisor.start_soon(noop(), owner="test", task_id="missing-correlation", name="noop")

    with bind_telemetry_context(correlation_id="corr-test"):
        supervisor.start_soon(noop(), owner="test", task_id="ok", name="noop")
    await supervisor.shutdown()
    record = supervisor.get_task("ok")
    assert record is not None
    assert record.owner == "test"
    assert record.correlation_id == "corr-test"


@pytest.mark.asyncio
async def test_compatibility_create_task_generates_correlation_id() -> None:
    supervisor = TaskSupervisor(TaskSupervisorConfig(max_concurrent_tasks=1))

    async def noop() -> None:
        return None

    task = supervisor.create_task(noop(), owner="legacy", task_id="legacy-task", name="legacy")
    await task

    record = supervisor.get_task("legacy-task")
    assert record is not None
    assert record.status == "completed"
    assert record.owner == "legacy"
    assert record.correlation_id
