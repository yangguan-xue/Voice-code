"""Disposable sandbox workspaces for the web demo."""

from __future__ import annotations

import asyncio
import difflib
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from voice_code.platform_fs import best_effort_private_permissions

_SANDBOX_ID = re.compile(r"[A-Za-z0-9_-]+")
_IGNORED_TEMPLATE_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
_IGNORED_DIFF_PARTS = {
    ".reasoning",
    "__pycache__",
}


class SandboxBoundaryError(ValueError):
    """Raised when sandbox setup or path resolution would escape isolation."""


@dataclass(frozen=True, slots=True)
class SandboxChangedFile:
    path: str
    status: str
    additions: int = 0
    deletions: int = 0

    def to_payload(self) -> dict[str, object]:
        return {
            "path": self.path,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
        }


@dataclass(frozen=True, slots=True)
class SandboxDiff:
    changed_files: list[SandboxChangedFile]
    patch: str
    disk_usage_bytes: int

    @property
    def patch_available(self) -> bool:
        return bool(self.patch.strip())

    def to_payload(self, *, include_patch: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "changedFiles": [item.to_payload() for item in self.changed_files],
            "patchAvailable": self.patch_available,
            "diskUsageBytes": self.disk_usage_bytes,
        }
        if include_patch:
            payload["patch"] = self.patch
        return payload


@dataclass(frozen=True, slots=True)
class SandboxUsage:
    file_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class SandboxFile:
    path: str
    content: str

    def to_payload(self) -> dict[str, object]:
        return {
            "path": self.path,
            "content": self.content,
        }


@dataclass(frozen=True, slots=True)
class DemoSandbox:
    session_id: str
    path: Path
    workspace_label: str

    def resolve_path(self, user_path: str | Path, *, allow_missing: bool = True) -> Path:
        raw_path = Path(user_path).expanduser()
        candidate = raw_path if raw_path.is_absolute() else self.path / raw_path
        try:
            resolved = candidate.resolve(strict=not allow_missing)
            resolved.relative_to(self.path.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise SandboxBoundaryError("Path escapes the demo sandbox.") from exc
        return resolved


class SandboxManager:
    """Create, reset, diff, and discard direct-child sandbox workspaces."""

    def __init__(self, root: str | Path, *, template_repo: str | Path | None = None) -> None:
        raw_root = Path(root).expanduser()
        if raw_root.exists() and raw_root.is_symlink():
            raise SandboxBoundaryError("Sandbox root must not be a symlink.")
        raw_root.mkdir(parents=True, exist_ok=True)
        resolved_root = raw_root.resolve(strict=True)
        self._validate_root(resolved_root)
        self.root = resolved_root
        best_effort_private_permissions(self.root, directory=True)

        self.template_repo: Path | None = None
        if template_repo is not None:
            raw_template = Path(template_repo).expanduser()
            if raw_template.is_symlink():
                raise SandboxBoundaryError("Template repo must not be a symlink.")
            resolved_template = raw_template.resolve(strict=True)
            if not resolved_template.is_dir():
                raise SandboxBoundaryError("Template repo must be a directory.")
            self.template_repo = resolved_template

    async def create(self, session_id: str) -> DemoSandbox:
        target = self._target(session_id)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Demo sandbox already exists: {session_id}")
        await asyncio.to_thread(self._populate, target)
        return DemoSandbox(
            session_id=session_id,
            path=target,
            workspace_label=self._workspace_label(target),
        )

    async def reset(self, sandbox: DemoSandbox) -> DemoSandbox:
        target = self._existing_target(sandbox)
        await asyncio.to_thread(_remove_tree, target)
        await asyncio.to_thread(self._populate, target)
        return DemoSandbox(
            session_id=sandbox.session_id,
            path=target,
            workspace_label=sandbox.workspace_label,
        )

    async def discard(self, sandbox: DemoSandbox) -> None:
        target = self._existing_target(sandbox)
        await asyncio.to_thread(_remove_tree, target)

    async def diff(self, sandbox: DemoSandbox) -> SandboxDiff:
        target = self._existing_target(sandbox)
        pathspec = self._diff_pathspec()
        status_by_path = _parse_porcelain(
            await self._git(target, "status", "--porcelain=v1", "--", *pathspec)
        )
        stats = _parse_numstat(
            await self._git(target, "diff", "--numstat", "HEAD", "--", *pathspec)
        )
        patch = await self._git(
            target,
            "diff",
            "--no-ext-diff",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "HEAD",
            "--",
            *pathspec,
        )
        changed_files = [
            SandboxChangedFile(
                path=path,
                status=status,
                additions=stats.get(path, (0, 0))[0],
                deletions=stats.get(path, (0, 0))[1],
            )
            for path, status in sorted(status_by_path.items())
        ]
        changed_files = [
            _with_untracked_stats(target, item) if item.status == "added" else item
            for item in changed_files
        ]
        patch = _append_added_file_patches(target, changed_files, patch)
        return SandboxDiff(
            changed_files=changed_files,
            patch=patch,
            disk_usage_bytes=_disk_usage(target),
        )

    async def read_file(self, sandbox: DemoSandbox, relative_path: str) -> SandboxFile:
        target = self._existing_target(sandbox)
        resolved = sandbox.resolve_path(relative_path, allow_missing=False)
        if not resolved.is_file():
            raise FileNotFoundError(relative_path)
        try:
            content = await asyncio.to_thread(
                resolved.read_text,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise FileNotFoundError(relative_path) from exc
        return SandboxFile(
            path=str(resolved.relative_to(target)).replace("\\", "/"),
            content=content,
        )

    @staticmethod
    def _diff_pathspec() -> tuple[str, ...]:
        return (
            ".",
            ":(glob,exclude)**/__pycache__/**",
            ":(glob,exclude)**/.reasoning/**",
            ":(exclude)__pycache__",
            ":(exclude).reasoning",
        )

    def _populate(self, target: Path) -> None:
        if self.template_repo is None:
            target.mkdir(parents=True)
            _write_default_template(target)
        else:
            shutil.copytree(
                self.template_repo,
                target,
                ignore=shutil.ignore_patterns(*_IGNORED_TEMPLATE_NAMES),
            )
        best_effort_private_permissions(target, directory=True)
        _init_git_baseline(target)

    def _target(self, session_id: str) -> Path:
        if not _SANDBOX_ID.fullmatch(session_id):
            raise SandboxBoundaryError("Sandbox id contains unsupported characters.")
        target = self.root / session_id
        try:
            target.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise SandboxBoundaryError("Sandbox path escapes the root.") from exc
        return target

    def _existing_target(self, sandbox: DemoSandbox) -> Path:
        target = self._target(sandbox.session_id)
        if target != sandbox.path.resolve(strict=False):
            raise SandboxBoundaryError("Sandbox path is not managed by this root.")
        if target.is_symlink() or not target.is_dir():
            raise SandboxBoundaryError("Sandbox path is missing or unsafe.")
        return target

    def _workspace_label(self, target: Path) -> str:
        if self.template_repo is None:
            return "demo-python-app"
        return self.template_repo.name or target.name

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

    @staticmethod
    def _validate_root(root: Path) -> None:
        resolved_home = Path.home().resolve()
        current_workspace = Path.cwd().resolve()
        if root == Path("/").resolve() or root == resolved_home:
            raise SandboxBoundaryError("Sandbox root is too broad.")
        if root == current_workspace:
            raise SandboxBoundaryError("Sandbox root must not be the app checkout.")
        if root.parent == root:
            raise SandboxBoundaryError("Sandbox root is unsafe.")


def _write_default_template(target: Path) -> None:
    (target / "README.md").write_text(
        "# Demo Python App\n\n"
        "A tiny project for trying the Voice Code web demo agent.\n",
        encoding="utf-8",
    )
    (target / "app.py").write_text(
        "def greeting(name: str = \"world\") -> str:\n"
        "    return f\"hello, {name}\"\n\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    print(greeting())\n",
        encoding="utf-8",
    )
    (target / "test_app.py").write_text(
        "from app import greeting\n\n\n"
        "def test_greeting_default() -> None:\n"
        "    assert greeting() == \"hello, world\"\n",
        encoding="utf-8",
    )


def _remove_tree(target: Path) -> None:
    """Remove a sandbox including read-only Git object files on Windows."""

    def clear_readonly_and_retry(
        func: Callable[[str], object], path: str, exc_info: BaseException
    ) -> None:
        del exc_info
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            pass
        func(path)

    shutil.rmtree(target, onexc=clear_readonly_and_retry)


def _init_git_baseline(target: Path) -> None:
    commands = [
        ["git", "init", "-q"],
        ["git", "config", "user.email", "web-demo@localhost"],
        ["git", "config", "user.name", "Voice Code Web Demo"],
        ["git", "add", "-A"],
        ["git", "commit", "--allow-empty", "-qm", "demo sandbox baseline"],
    ]
    for command in commands:
        subprocess.run(command, cwd=target, check=True)


def _parse_porcelain(output: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in output.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        path_start = 3 if len(line) > 2 and line[2] == " " else 2
        path = line[path_start:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if os.path.isabs(path) or _is_ignored_diff_path(path):
            continue
        if code == "??" or "A" in code:
            statuses[path] = "added"
        elif "D" in code:
            statuses[path] = "deleted"
        else:
            statuses[path] = "modified"
    return statuses


def _parse_numstat(output: str) -> dict[str, tuple[int, int]]:
    stats: dict[str, tuple[int, int]] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        additions = int(parts[0]) if parts[0].isdigit() else 0
        deletions = int(parts[1]) if parts[1].isdigit() else 0
        path = parts[2]
        if not os.path.isabs(path) and not _is_ignored_diff_path(path):
            stats[path] = (additions, deletions)
    return stats


def _is_ignored_diff_path(path: str) -> bool:
    return any(part in _IGNORED_DIFF_PARTS for part in Path(path).parts)


def _with_untracked_stats(root: Path, item: SandboxChangedFile) -> SandboxChangedFile:
    path = root / item.path
    if not path.is_file():
        return item
    try:
        additions = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        additions = 0
    return SandboxChangedFile(
        path=item.path,
        status=item.status,
        additions=additions,
        deletions=item.deletions,
    )


def _append_added_file_patches(
    root: Path,
    changed_files: list[SandboxChangedFile],
    patch: str,
) -> str:
    extra_patches: list[str] = []
    existing_patch = patch.strip()
    for item in changed_files:
        if item.status != "added":
            continue
        if f"b/{item.path}" in patch or f"+++ b/{item.path}" in patch:
            continue
        extra_patch = _render_added_file_patch(root, item.path)
        if extra_patch:
            extra_patches.append(extra_patch)
    if not extra_patches:
        return patch
    if not existing_patch:
        return "\n".join(extra_patches)
    return f"{patch.rstrip()}\n\n" + "\n\n".join(extra_patches)


def _render_added_file_patch(root: Path, relative_path: str) -> str:
    path = root / relative_path
    if not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        return ""
    diff_lines = list(
        difflib.unified_diff(
            [],
            content,
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            lineterm="",
        )
    )
    if len(diff_lines) < 2:
        return ""
    header = [
        f"diff --git a/{relative_path} b/{relative_path}",
        "new file mode 100644",
        "index 0000000..0000000",
    ]
    return "\n".join(header + diff_lines)


def _disk_usage(root: Path) -> int:
    total = 0
    file_count = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
                file_count += 1
        except OSError:
            continue
    return total


def workspace_usage(root: Path) -> SandboxUsage:
    total = 0
    file_count = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
                file_count += 1
        except OSError:
            continue
    return SandboxUsage(file_count=file_count, total_bytes=total)
