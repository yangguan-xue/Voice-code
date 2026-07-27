"""Isolated Git worktrees and checkpoints for durable goals."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from voice_code.platform_fs import best_effort_private_permissions

_GOAL_ID = re.compile(r"[a-z0-9_-]+")
_COMMIT_ID = re.compile(r"[0-9a-f]{40,64}")


@dataclass(frozen=True, slots=True)
class GoalWorkspace:
    path: Path
    baseline_ref: str


class GoalWorkspaceManager:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        state_root = self.workspace / ".reasoning"
        if state_root.is_symlink():
            raise ValueError("goal state directory must not be a symlink")
        self.root = state_root / "worktrees"
        if self.root.is_symlink():
            raise ValueError("goal worktree directory must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.resolve().relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("goal worktree directory must stay inside the workspace") from exc
        best_effort_private_permissions(self.root, directory=True)

    async def prepare(self, goal_id: str) -> GoalWorkspace:
        target = self._target(goal_id)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"goal worktree already exists: {target}")
        repository_root, relative_workspace = await self._repository_layout()
        baseline_ref = await self._git(repository_root, "rev-parse", "HEAD")
        await self._create(repository_root, target, baseline_ref)
        return GoalWorkspace(
            path=target / relative_workspace,
            baseline_ref=baseline_ref,
        )

    async def checkpoint(self, goal_id: str, *, iteration: int) -> str:
        target = self._existing_target(goal_id)
        await self._git(target, "add", "-A", "--", ".")
        has_changes = await self._has_staged_changes(target)
        if has_changes:
            await self._git(
                target,
                "-c",
                "user.name=Reasoning Goal Loop",
                "-c",
                "user.email=goal-loop@localhost",
                "commit",
                "-qm",
                f"goal checkpoint iteration {iteration}",
            )
        return await self._git(target, "rev-parse", "HEAD")

    async def attach(self, goal_id: str, baseline_ref: str) -> GoalWorkspace:
        if not _COMMIT_ID.fullmatch(baseline_ref):
            raise ValueError("baseline must be a full commit ID")
        target = self._existing_target(goal_id)
        repository_root, relative_workspace = await self._repository_layout()
        registered_root = Path(await self._git(target, "rev-parse", "--show-toplevel"))
        if registered_root.resolve() != target.resolve():
            raise ValueError("goal worktree registration does not match its managed path")
        await self._git(
            repository_root,
            "rev-parse",
            "--verify",
            f"{baseline_ref}^{{commit}}",
        )
        execution_workspace = target / relative_workspace
        if not execution_workspace.is_dir():
            raise FileNotFoundError(f"goal execution workspace not found: {execution_workspace}")
        return GoalWorkspace(path=execution_workspace, baseline_ref=baseline_ref)

    async def restore(self, goal_id: str, checkpoint_ref: str) -> GoalWorkspace:
        if not _COMMIT_ID.fullmatch(checkpoint_ref):
            raise ValueError("checkpoint must be a full commit ID")
        repository_root, relative_workspace = await self._repository_layout()
        verified_ref = await self._git(
            repository_root,
            "rev-parse",
            "--verify",
            f"{checkpoint_ref}^{{commit}}",
        )
        target = self._existing_target(goal_id)
        await self._git(repository_root, "worktree", "remove", "--force", str(target))
        await self._create(repository_root, target, verified_ref)
        return GoalWorkspace(
            path=target / relative_workspace,
            baseline_ref=verified_ref,
        )

    async def discard(self, goal_id: str) -> None:
        target = self._target(goal_id)
        if target.is_symlink():
            raise ValueError("goal worktree path must not be a symlink")
        if target.exists():
            repository_root, _ = await self._repository_layout()
            await self._git(repository_root, "worktree", "remove", "--force", str(target))

    async def _create(
        self,
        repository_root: Path,
        target: Path,
        commit_ref: str,
    ) -> None:
        await self._git(
            repository_root,
            "worktree",
            "add",
            "--detach",
            str(target),
            commit_ref,
        )

    async def _repository_layout(self) -> tuple[Path, Path]:
        raw_root = await self._git(self.workspace, "rev-parse", "--show-toplevel")
        repository_root = Path(raw_root).resolve(strict=True)
        try:
            relative_workspace = self.workspace.relative_to(repository_root)
        except ValueError as exc:
            raise ValueError("goal workspace must be inside the Git repository") from exc
        return repository_root, relative_workspace

    async def _has_staged_changes(self, target: Path) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "--cached",
            "--quiet",
            "--exit-code",
            cwd=str(target),
        )
        await proc.communicate()
        if proc.returncode not in {0, 1}:
            raise RuntimeError("unable to inspect staged goal changes")
        return proc.returncode == 1

    async def _git(self, cwd: Path, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or f"git {' '.join(args)} failed")
        return stdout.decode("utf-8", errors="replace").strip()

    def _existing_target(self, goal_id: str) -> Path:
        target = self._target(goal_id)
        if target.is_symlink() or not target.is_dir():
            raise FileNotFoundError(f"goal worktree not found: {target}")
        return target

    def _target(self, goal_id: str) -> Path:
        if not _GOAL_ID.fullmatch(goal_id):
            raise ValueError("goal id contains unsupported characters")
        return self.root / goal_id
