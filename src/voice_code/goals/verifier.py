"""Deterministic command gates for GoalLoop."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from pathlib import Path

from voice_code.goals.scope import verify_changed_paths
from voice_code.goals.types import GoalSpec, VerificationEvidence


async def run_verification_command(
    command: str,
    *,
    workspace: str | Path,
    timeout_seconds: float = 300,
) -> VerificationEvidence:
    started = time.monotonic()
    proc = await _create_verification_process(command, workspace=workspace)
    timed_out = False
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout_seconds)
    except TimeoutError:
        timed_out = True
        _kill_process_group(proc.pid)
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        _kill_process_group(proc.pid)
        await proc.communicate()
        raise

    return VerificationEvidence(
        command=command,
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout.decode("utf-8", errors="replace")[-20_000:],
        stderr=stderr.decode("utf-8", errors="replace")[-20_000:],
        duration_ms=int((time.monotonic() - started) * 1000),
        timed_out=timed_out,
    )


def _kill_process_group(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _create_verification_process(
    command: str,
    *,
    workspace: str | Path,
) -> asyncio.subprocess.Process:
    common = {
        "cwd": str(Path(workspace).resolve()),
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if os.name == "nt":
        return await asyncio.create_subprocess_shell(
            _windows_shell_command(command),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            **common,
        )
    return await asyncio.create_subprocess_exec(
        os.environ.get("SHELL", "bash"),
        "-c",
        command,
        start_new_session=True,
        **common,
    )


def _windows_shell_command(command: str) -> str:
    normalized = command.strip()
    if normalized == "true":
        return "exit /b 0"
    if normalized == "false":
        return "exit /b 1"
    return command


async def verify_goal(spec: GoalSpec) -> list[VerificationEvidence]:
    evidence: list[VerificationEvidence] = []
    for command in spec.verification_commands:
        evidence.append(
            await run_verification_command(command, workspace=spec.workspace)
        )
    if spec.allowed_paths:
        evidence.append(
            await verify_changed_paths(
                spec.workspace,
                spec.allowed_paths,
                baseline_ref=spec.baseline_ref,
            )
        )
    return evidence
