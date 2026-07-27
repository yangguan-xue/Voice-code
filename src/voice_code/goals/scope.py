"""Deterministic Git diff scope gate for goal workspaces."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path, PurePosixPath

from voice_code.goals.types import VerificationEvidence

_SCOPE_COMMAND = "git diff --scope"
_COMMIT_ID = re.compile(r"[0-9a-f]{40,64}")
_WINDOWS_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:[/\\]")
_WINDOWS_ROOT_ABSOLUTE = re.compile(r"^[/\\]{1,2}[^/\\]")


def normalize_allowed_paths(paths: list[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw_path in paths:
        raw_value = raw_path.strip()
        value = raw_value.replace("\\", "/")
        candidate = PurePosixPath(value)
        if (
            not value
            or candidate.is_absolute()
            or _WINDOWS_DRIVE_ABSOLUTE.match(raw_value)
            or _WINDOWS_ROOT_ABSOLUTE.match(raw_value)
            or ".." in candidate.parts
            or "\x00" in value
        ):
            raise ValueError(f"allowed path must stay inside the repository: {raw_path}")
        clean = candidate.as_posix().removeprefix("./").rstrip("/")
        if clean == ".":
            clean = ""
        if not clean:
            clean = "."
        if clean == "." and value not in {".", "./"}:
            raise ValueError(f"allowed path must name a repository entry: {raw_path}")
        if clean not in normalized:
            normalized.append(clean)
    return tuple(normalized)


async def verify_changed_paths(
    workspace: str | Path,
    allowed_paths: list[str],
    *,
    baseline_ref: str = "HEAD",
) -> VerificationEvidence:
    started = time.monotonic()
    normalized = normalize_allowed_paths(allowed_paths)
    baseline = baseline_ref or "HEAD"
    if baseline != "HEAD" and not _COMMIT_ID.fullmatch(baseline):
        raise ValueError("baseline must be HEAD or a full commit ID")
    try:
        workspace_path = Path(workspace).resolve(strict=True)
        root_text = await _git_stdout(workspace_path, "rev-parse", "--show-toplevel")
        repository_root = Path(root_text.decode("utf-8", errors="replace").strip()).resolve()
        relative_workspace = workspace_path.relative_to(repository_root)
        prefix = relative_workspace.as_posix().rstrip("/")
        tracked = await _git_paths(
            repository_root,
            "diff",
            "--name-only",
            "-z",
            baseline,
            "--",
        )
        untracked = await _git_paths(
            repository_root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
        )
    except RuntimeError as exc:
        return _evidence(started, exit_code=1, stderr=str(exc))

    changed_at_root = sorted(set(tracked + untracked))
    changed: list[str] = []
    violations: list[str] = []
    for path in changed_at_root:
        local_path = _workspace_relative_path(path, prefix)
        display_path = local_path if local_path is not None else f"<outside-workspace>/{path}"
        changed.append(display_path)
        if local_path is None or not _is_allowed(local_path, normalized):
            violations.append(display_path)
    return _evidence(
        started,
        exit_code=1 if violations else 0,
        stdout="\n".join(changed),
        stderr=(
            "Changes outside allowed paths:\n" + "\n".join(violations)
            if violations
            else ""
        ),
    )


def _is_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    candidate = PurePosixPath(path).as_posix().removeprefix("./")
    if "." in allowed_paths:
        return True
    return any(
        candidate == allowed or candidate.startswith(f"{allowed}/")
        for allowed in allowed_paths
    )


async def _git_paths(workspace: str | Path, *args: str) -> list[str]:
    stdout = await _git_stdout(workspace, *args)
    return [
        value.decode("utf-8", errors="replace")
        for value in stdout.split(b"\0")
        if value
    ]


async def _git_stdout(workspace: str | Path, *args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(Path(workspace).resolve()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"git {' '.join(args)} failed")
    return stdout


def _workspace_relative_path(path: str, prefix: str) -> str | None:
    if not prefix or prefix == ".":
        return path
    if path == prefix:
        return ""
    marker = f"{prefix}/"
    return path.removeprefix(marker) if path.startswith(marker) else None


def _evidence(
    started: float,
    *,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
) -> VerificationEvidence:
    return VerificationEvidence(
        command=_SCOPE_COMMAND,
        exit_code=exit_code,
        stdout=stdout[-20_000:],
        stderr=stderr[-20_000:],
        duration_ms=int((time.monotonic() - started) * 1000),
    )
