"""Durable inbox for isolated delegated engineering tasks."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from voice_code.delegation.types import (
    DelegationBrief,
    DelegationTask,
    DelegationTaskStatus,
)
from voice_code.delegation.worktree import WorktreeManager
from voice_code.platform_fs import best_effort_private_permissions

DelegatedRunner = Callable[[DelegationBrief, Path], Awaitable[str]]


class DelegationService:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        self.manager = WorktreeManager(self.workspace)
        self.inbox = self.workspace / ".reasoning" / "delegations"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self._running: dict[str, asyncio.Task[None]] = {}

    def submit(self, brief: DelegationBrief, runner: DelegatedRunner) -> DelegationTask:
        task = DelegationTask(brief=brief, created_at=time.time(), updated_at=time.time())
        self._save(task)
        background = asyncio.create_task(self._run(task, runner))
        self._running[brief.task_id] = background
        background.add_done_callback(lambda _: self._running.pop(brief.task_id, None))
        return task

    async def _run(self, task: DelegationTask, runner: DelegatedRunner) -> None:
        try:
            task.status = DelegationTaskStatus.RUNNING
            worktree = await self.manager.create(task.brief.task_id)
            task.worktree_path = str(worktree)
            self._save(task)
            task.result_summary = await runner(task.brief, worktree)
            patch = await self.manager.create_patch(task.brief.task_id)
            task.patch_path = str(patch)
            task.status = DelegationTaskStatus.COMPLETED
        except asyncio.CancelledError:
            task.status = DelegationTaskStatus.CANCELLED
            task.error = "cancelled by user"
        except Exception as exc:
            task.status = DelegationTaskStatus.FAILED
            task.error = str(exc)
        finally:
            self._save(task)

    async def accept_into_workspace(self, task_id: str) -> DelegationTask:
        task = self.load(task_id)
        if task.status != DelegationTaskStatus.COMPLETED:
            raise ValueError("Only completed delegation tasks can be accepted")
        await self.manager.apply_patch(task.patch_path)
        task.status = DelegationTaskStatus.ACCEPTED_INTO_WORKSPACE
        self._save(task)
        return task

    async def discard(self, task_id: str) -> DelegationTask:
        task = self.load(task_id)
        running = self._running.get(task_id)
        if running is not None:
            running.cancel()
        await self.manager.discard(task_id)
        task.status = DelegationTaskStatus.DISCARDED
        self._save(task)
        return task

    def load(self, task_id: str) -> DelegationTask:
        payload = json.loads((self.inbox / f"{task_id}.json").read_text(encoding="utf-8"))
        brief = DelegationBrief(**payload.pop("brief"))
        payload["status"] = DelegationTaskStatus(payload["status"])
        return DelegationTask(brief=brief, **payload)

    def list_tasks(self) -> list[DelegationTask]:
        return [self.load(path.stem) for path in sorted(self.inbox.glob("*.json"))]

    async def wait(self, task_id: str) -> DelegationTask:
        running = self._running.get(task_id)
        if running is not None:
            await running
        return self.load(task_id)

    def _save(self, task: DelegationTask) -> None:
        task.updated_at = time.time()
        path = self.inbox / f"{task.brief.task_id}.json"
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.inbox)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(task.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            best_effort_private_permissions(temp_path)
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)
