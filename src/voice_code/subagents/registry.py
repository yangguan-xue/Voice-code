"""子 agent 任务注册表。"""

from __future__ import annotations

import threading
import time
from dataclasses import replace

from voice_code.subagents.events import TaskEvent, TaskEventType
from voice_code.subagents.types import (
    AgentTask,
    TaskProgress,
    TaskStatus,
    TaskSummary,
)


class TaskRegistry:
    """管理子 agent 任务生命周期的进程内注册表。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, AgentTask] = {}
        self._events: list[TaskEvent] = []

    def create_task(self, task: AgentTask) -> None:
        with self._lock:
            if task.task_id in self._tasks:
                raise ValueError(f"Task '{task.task_id}' already exists")
            self._tasks[task.task_id] = task
            self._append_event(task, TaskEventType.CREATED)

    def get_task(self, task_id: str) -> AgentTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return replace(task) if task is not None else None

    def list_tasks(self, *, session_id: str | None = None) -> list[AgentTask]:
        with self._lock:
            tasks = self._tasks.values()
            if session_id is not None:
                tasks = [task for task in tasks if task.session_id == session_id]
            return [replace(task) for task in sorted(tasks, key=lambda item: item.created_at)]

    def update_status(self, task_id: str, status: TaskStatus, *, error: str | None = None) -> None:
        with self._lock:
            task = self._require_task(task_id)
            now = time.time()
            task.status = status
            if status == TaskStatus.RUNNING and task.started_at is None:
                task.started_at = now
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                task.finished_at = now
            if error is not None:
                task.error = error
            self._append_event(task, _status_to_event_type(status), message=error or "")

    def update_progress(self, task_id: str, progress: TaskProgress) -> None:
        with self._lock:
            task = self._require_task(task_id)
            task.progress = progress
            self._append_event(task, TaskEventType.PROGRESS, message=progress.summary)

    def request_stop(self, task_id: str) -> None:
        with self._lock:
            task = self._require_task(task_id)
            task.stop_requested = True
            self._append_event(task, TaskEventType.STOP_REQUESTED, message="stop requested")

    def complete_task(self, task_id: str, summary: TaskSummary) -> None:
        with self._lock:
            task = self._require_task(task_id)
            task.status = TaskStatus.COMPLETED
            task.finished_at = time.time()
            task.result_summary = summary.summary
            task.transcript_path = summary.transcript_path
            if task.progress is None:
                task.progress = TaskProgress(
                    tool_use_count=summary.tool_uses,
                    token_count=summary.total_tokens,
                    summary=summary.summary,
                )
            self._append_event(task, TaskEventType.COMPLETED, message=summary.summary)

    def fail_task(self, task_id: str, *, error: str) -> None:
        self.update_status(task_id, TaskStatus.FAILED, error=error)

    def cancel_task(self, task_id: str, *, error: str = "") -> None:
        self.update_status(task_id, TaskStatus.CANCELLED, error=error)

    def drain_events(self) -> list[TaskEvent]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def _require_task(self, task_id: str) -> AgentTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task_id: {task_id}")
        return task

    def _append_event(
        self,
        task: AgentTask,
        event_type: TaskEventType,
        *,
        message: str = "",
    ) -> None:
        self._events.append(
            TaskEvent(
                type=event_type,
                task_id=task.task_id,
                session_id=task.session_id,
                agent_type=task.agent_type,
                status=task.status,
                timestamp=time.time(),
                message=message,
            )
        )


def _status_to_event_type(status: TaskStatus) -> TaskEventType:
    if status == TaskStatus.RUNNING:
        return TaskEventType.STARTED
    if status == TaskStatus.WAITING_PERMISSION:
        return TaskEventType.WAITING_PERMISSION
    if status == TaskStatus.COMPLETED:
        return TaskEventType.COMPLETED
    if status == TaskStatus.FAILED:
        return TaskEventType.FAILED
    if status == TaskStatus.CANCELLED:
        return TaskEventType.CANCELLED
    return TaskEventType.PROGRESS
