from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal

from voice_code.telemetry.context import (
    bind_telemetry_context,
    current_telemetry_context,
    new_correlation_id,
)

TaskStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "rejected",
    "shutdown_timeout",
]


class TaskRejectedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TaskSupervisorConfig:
    max_concurrent_tasks: int = 32
    shutdown_timeout: float = 5.0


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    owner: str
    name: str
    correlation_id: str
    status: TaskStatus = "pending"
    started_at: float | None = None
    ended_at: float | None = None
    exception_type: str | None = None


@dataclass(frozen=True, slots=True)
class TaskShutdownReport:
    cancelled: list[str] = field(default_factory=list)
    timed_out: list[str] = field(default_factory=list)


class TaskSupervisor:
    def __init__(self, config: TaskSupervisorConfig | None = None) -> None:
        self._config = config or TaskSupervisorConfig()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._records: dict[str, TaskRecord] = {}
        self._accepting = True

    def start_soon(
        self,
        awaitable: Awaitable[Any] | Coroutine[Any, Any, Any],
        *,
        owner: str,
        task_id: str,
        name: str,
    ) -> asyncio.Task[Any]:
        if not owner:
            _close_awaitable(awaitable)
            raise ValueError("Task owner is required")
        correlation_id = current_telemetry_context().correlation_id
        if not correlation_id:
            _close_awaitable(awaitable)
            raise ValueError("Task correlation_id is required")
        record = TaskRecord(
            task_id=task_id,
            owner=owner,
            name=name,
            correlation_id=correlation_id,
            status="pending",
        )
        if not self._accepting or len(self._tasks) >= self._config.max_concurrent_tasks:
            record.status = "rejected"
            record.ended_at = time.time()
            self._records[task_id] = record
            _close_awaitable(awaitable)
            raise TaskRejectedError(f"Task rejected: {task_id}")
        record.status = "running"
        record.started_at = time.time()
        task = asyncio.create_task(awaitable, name=name)
        self._tasks[task_id] = task
        self._records[task_id] = record
        task.add_done_callback(
            lambda completed, record_id=task_id: self._consume_done(record_id, completed)
        )
        return task

    def create_task(
        self,
        awaitable: Awaitable[Any] | Coroutine[Any, Any, Any],
        *,
        owner: str,
        task_id: str,
        name: str,
    ) -> asyncio.Task[Any]:
        correlation_id = current_telemetry_context().correlation_id
        if correlation_id:
            return self.start_soon(awaitable, owner=owner, task_id=task_id, name=name)
        with bind_telemetry_context(correlation_id=new_correlation_id()):
            return self.start_soon(awaitable, owner=owner, task_id=task_id, name=name)

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def active_task_ids(self) -> list[str]:
        return list(self._tasks)

    async def shutdown(self) -> TaskShutdownReport:
        self._accepting = False
        task_ids = list(self._tasks)
        for task_id in task_ids:
            self._tasks[task_id].cancel()
        if not task_ids:
            return TaskShutdownReport()
        done, pending = await asyncio.wait(
            [self._tasks[task_id] for task_id in task_ids],
            timeout=self._config.shutdown_timeout,
        )
        for task in done:
            task_id = _task_id_for(self._tasks, task)
            if task_id is not None:
                self._consume_done(task_id, task)
        timed_out: list[str] = []
        for task in pending:
            task_id = _task_id_for(self._tasks, task)
            if task_id is None:
                continue
            timed_out.append(task_id)
            record = self._records[task_id]
            record.status = "shutdown_timeout"
            record.ended_at = time.time()
            self._tasks.pop(task_id, None)
        cancelled = [task_id for task_id in task_ids if task_id not in timed_out]
        return TaskShutdownReport(cancelled=cancelled, timed_out=timed_out)

    def _consume_done(self, task_id: str, task: asyncio.Task[Any]) -> None:
        record = self._records.get(task_id)
        if record is None or record.status == "shutdown_timeout":
            return
        self._tasks.pop(task_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            record.status = "cancelled"
        except Exception as exc:
            record.status = "failed"
            record.exception_type = type(exc).__name__
        else:
            record.status = "completed"
        record.ended_at = time.time()


def _task_id_for(tasks: dict[str, asyncio.Task[Any]], task: asyncio.Task[Any]) -> str | None:
    for task_id, candidate in tasks.items():
        if candidate is task:
            return task_id
    return None


def _close_awaitable(awaitable: Awaitable[Any] | Coroutine[Any, Any, Any]) -> None:
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()
