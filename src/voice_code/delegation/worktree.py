"""Git worktree isolation and explicit patch handoff."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path


class WorktreeManager:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / ".reasoning" / "worktrees"
        self.root.mkdir(parents=True, exist_ok=True)

    async def create(self, task_id: str) -> Path:
        self._validate_task_id(task_id)
        target = self.root / task_id
        if target.exists():
            raise FileExistsError(f"Delegation worktree already exists: {target}")
        await self._git("worktree", "add", "--detach", str(target), "HEAD")
        return target

    async def create_patch(self, task_id: str) -> Path:
        self._validate_task_id(task_id)
        target = self.root / task_id
        if not target.exists():
            raise FileNotFoundError(f"Delegation worktree not found: {target}")
        patch_path = self.root / f"{task_id}.patch"
        add_proc = await asyncio.create_subprocess_exec(
            "git",
            "add",
            "-N",
            "--",
            ".",
            cwd=str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, add_stderr = await add_proc.communicate()
        if add_proc.returncode != 0:
            raise RuntimeError(add_stderr.decode("utf-8", errors="replace").strip())
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "--binary",
            "HEAD",
            cwd=str(target),
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("Unable to create delegation patch")
        patch_path.write_bytes(stdout)
        return patch_path

    async def apply_patch(self, patch_path: str | Path) -> None:
        patch = Path(patch_path).resolve()
        if not patch.is_file() or patch.parent != self.root:
            raise ValueError("Patch is not managed by this workspace")
        await self._git("apply", "--check", str(patch))
        await self._git("apply", "--3way", str(patch))

    async def discard(self, task_id: str) -> None:
        self._validate_task_id(task_id)
        target = self.root / task_id
        if target.exists():
            await self._git("worktree", "remove", "--force", str(target))
        shutil.rmtree(target, ignore_errors=True)

    async def _git(self, *args: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())

    @staticmethod
    def _validate_task_id(task_id: str) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", task_id):
            raise ValueError("Delegation task id contains unsupported characters")
