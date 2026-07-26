"""Docker-backed Linux sandbox for web demo shell execution."""

from __future__ import annotations

import asyncio
import os
import secrets
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool, StructuredTool

from voice_code.audit import record_audit_event
from voice_code.security import WorkspaceBoundaryError, resolve_workspace_path
from voice_code.tools.bash import BashSandboxPolicy, _has_write_intent, _normalize_policy
from voice_code.web_demo.sandbox import workspace_usage

if TYPE_CHECKING:
    from voice_code.web_demo.limits import WebDemoLimits

_MAX_OUTPUT_CHARS = 100_000
_DOCKER_SANDBOX_IMAGE_ENV = "REASONING_WEB_DEMO_SANDBOX_IMAGE"
_DEFAULT_SANDBOX_IMAGE = "python:3.13-slim"
_DEFAULT_CPU_SECONDS = 10
_DEFAULT_MEMORY_MB = 512
_DEFAULT_PIDS_LIMIT = 128
_DEFAULT_TIMEOUT_MS = 120000
_DOCKER_WORKSPACE = PurePosixPath("/workspace")
_PATH_ESCAPE_TOKENS = (
    "/etc",
    "/home",
    "/root",
    "/var",
    "/proc",
    "/sys",
    "/dev",
    "/tmp",
    "/workspace/..",
    "../",
    "..\\",
)
_BLOCKED_INTERPRETER_NAMES = frozenset(
    {
        "python",
        "python3",
        "python3.12",
        "node",
        "perl",
        "ruby",
        "php",
    }
)
_SHELL_CHAIN_TOKENS = {"&&", "||", ";", "|"}


@dataclass(frozen=True, slots=True)
class DockerSandboxConfig:
    workspace_root: Path
    image: str
    shell: str = "/bin/sh"
    cpu_seconds: int = _DEFAULT_CPU_SECONDS
    memory_mb: int = _DEFAULT_MEMORY_MB
    pids_limit: int = _DEFAULT_PIDS_LIMIT
    network: str = "none"


def create_web_demo_bash_tool(
    workspace_root: str | Path,
    *,
    limits: WebDemoLimits | None = None,
) -> BaseTool | None:
    """Create a bash-compatible tool that executes inside a Docker sandbox."""

    config = docker_sandbox_config(workspace_root)
    if config is None:
        return None

    def run_in_sandbox(
        command: str,
        description: str = "",
        timeout: int = _DEFAULT_TIMEOUT_MS,
        workdir: str = ".",
        policy: BashSandboxPolicy | dict[str, object] | None = None,
    ) -> str:
        del description
        active_limits = limits
        validation_error = _validate_web_demo_command(command)
        if validation_error is not None:
            return f"<tool_use_error>Error: {validation_error}</tool_use_error>"
        try:
            resolved_workdir = resolve_workspace_path(workdir, allow_missing=False)
        except (WorkspaceBoundaryError, FileNotFoundError) as exc:
            return f"<tool_use_error>Error: {exc}</tool_use_error>"
        if not resolved_workdir.is_dir():
            return f"<tool_use_error>Error: Workdir is not a directory: {workdir}</tool_use_error>"

        if active_limits is not None:
            limit_error = _quota_error_for_workspace(config.workspace_root, active_limits)
            if limit_error is not None:
                return f"<tool_use_error>Error: {limit_error}</tool_use_error>"

        active_policy = _normalize_policy(policy)
        if active_policy.allowlist:
            try:
                if not any(
                    resolved_workdir.is_relative_to(
                        resolve_workspace_path(item, allow_missing=False)
                    )
                    for item in active_policy.allowlist
                ):
                    return (
                        "<tool_use_error>Error: Workdir is outside the Bash allowlist"
                        "</tool_use_error>"
                    )
            except (WorkspaceBoundaryError, FileNotFoundError) as exc:
                return f"<tool_use_error>Error: {exc}</tool_use_error>"

        if _has_write_intent(command):
            record_audit_event(
                event_type="bash.write",
                actor="agent",
                resource_id="tool:bash",
                outcome="requested",
            )

        out, err, rc = asyncio.run(
            _run_docker_command(
                command=command,
                timeout_ms=timeout,
                workdir=resolved_workdir,
                config=config,
                policy=active_policy,
                limits=active_limits,
            )
        )

        parts: list[str] = []
        if out:
            parts.append(out)
        if err:
            parts.append(err)

        result = "\n".join(parts).strip() or "(no output)"
        if len(result) > _MAX_OUTPUT_CHARS:
            result = result[:_MAX_OUTPUT_CHARS] + "\n\n... (output truncated)"
        if rc != 0:
            result += f"\n\nExit code: {rc}"

        if active_limits is not None:
            limit_error = _quota_error_for_workspace(config.workspace_root, active_limits)
            if limit_error is not None:
                _restore_workspace_baseline(config.workspace_root)
                return f"<tool_use_error>Error: {limit_error}</tool_use_error>"
        return result

    tool = StructuredTool.from_function(
        func=run_in_sandbox,
        name="bash",
        description=(
            "Execute a shell command inside the Linux web demo sandbox container. "
            "The command only has access to the current demo workspace."
        ),
    )
    tool.metadata = {
        "is_readonly": False,
        "is_concurrency_safe": False,
        "max_result_chars": _MAX_OUTPUT_CHARS,
        "allow_discovery_commands": True,
    }
    return tool


def docker_sandbox_config(workspace_root: str | Path) -> DockerSandboxConfig | None:
    if os.name == "nt":
        return None
    docker_path = shutil.which("docker")
    if not docker_path:
        return None
    image = os.environ.get(_DOCKER_SANDBOX_IMAGE_ENV, _DEFAULT_SANDBOX_IMAGE).strip()
    if not image:
        return None
    if not _docker_image_available(image, docker_path=docker_path):
        return None
    return DockerSandboxConfig(
        workspace_root=Path(workspace_root).expanduser().resolve(strict=True),
        image=image,
    )


def _docker_image_available(image: str, *, docker_path: str = "docker") -> bool:
    completed = subprocess.run(
        [docker_path, "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _container_workdir(workspace_root: Path, workdir: Path) -> str:
    relative = workdir.relative_to(workspace_root)
    if not relative.parts:
        return str(_DOCKER_WORKSPACE)
    return str(_DOCKER_WORKSPACE.joinpath(*relative.parts))


def _build_docker_command(
    *,
    container_name: str,
    command: str,
    workdir: Path,
    config: DockerSandboxConfig,
    policy: BashSandboxPolicy,
    limits: WebDemoLimits | None = None,
) -> list[str]:
    cpu_seconds = policy.cpu_seconds or config.cpu_seconds
    memory_mb = policy.memory_mb or config.memory_mb
    file_size_blocks = None
    if limits is not None and limits.max_file_bytes > 0:
        file_size_blocks = max(1, limits.max_file_bytes // 512)
    container_workdir = _container_workdir(config.workspace_root, workdir)
    shell_command = _sandbox_shell_command(
        command=command,
        workdir=container_workdir,
        cpu_seconds=cpu_seconds,
        file_size_blocks=file_size_blocks,
    )
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        config.network,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        str(config.pids_limit),
        "--memory",
        f"{memory_mb}m",
        "--cpus",
        "1.0",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--tmpfs",
        "/run:rw,nosuid,size=16m",
        "--user",
        _docker_user_spec(),
        "-e",
        "HOME=/tmp",
        "-e",
        f"PYTHONUNBUFFERED={os.environ.get('PYTHONUNBUFFERED', '1')}",
        "-v",
        f"{config.workspace_root}:{_DOCKER_WORKSPACE}:rw",
        "-w",
        container_workdir,
        config.image,
        config.shell,
        "-lc",
        shell_command,
    ]


def _docker_user_spec() -> str:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if callable(getuid) and callable(getgid):
        return f"{getuid()}:{getgid()}"
    return "0:0"


async def _run_docker_command(
    *,
    command: str,
    timeout_ms: int,
    workdir: Path,
    config: DockerSandboxConfig,
    policy: BashSandboxPolicy,
    limits: WebDemoLimits | None = None,
) -> tuple[str, str, int]:
    container_name = f"reasoning-web-demo-{secrets.token_hex(6)}"
    docker_command = _build_docker_command(
        container_name=container_name,
        command=command,
        workdir=workdir,
        config=config,
        policy=policy,
        limits=limits,
    )
    proc = await asyncio.create_subprocess_exec(
        *docker_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_ms / 1000.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        await _force_remove_container(container_name)
        return (
            "",
            f"<tool_use_error>Error: Command timed out after {timeout_ms}ms</tool_use_error>",
            -1,
        )

    out = stdout.decode("utf-8", errors="replace") if stdout else ""
    err = stderr.decode("utf-8", errors="replace") if stderr else ""
    return out, err, proc.returncode or 0


async def _force_remove_container(container_name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "rm",
        "-f",
        container_name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()


def _sandbox_shell_command(
    *,
    command: str,
    workdir: str,
    cpu_seconds: int,
    file_size_blocks: int | None,
) -> str:
    shell_setup = [f"cd {shlex.quote(workdir)}", f"ulimit -t {cpu_seconds} >/dev/null 2>&1 || true"]
    if file_size_blocks is not None:
        shell_setup.append(f"ulimit -f {file_size_blocks} >/dev/null 2>&1 || true")
    return "; ".join(shell_setup + [command])


def _validate_web_demo_command(command: str) -> str | None:
    normalized = " ".join(command.strip().lower().split())
    if not normalized:
        return None
    if any(token in normalized for token in _PATH_ESCAPE_TOKENS):
        return "This command references paths outside the demo workspace."
    if _contains_blocked_interpreter(command):
        return "Interpreter commands are disabled in the web demo sandbox."
    if " ln -s" in f" {normalized}" or normalized.startswith("ln -s"):
        return "Symlink creation is disabled in the web demo sandbox."
    if (
        "<<'" in normalized
        or '<<"' in normalized
        or " <<" in normalized
        or "$(" in command
        or "`" in command
    ):
        return "Shell expansion patterns are disabled in the web demo sandbox."
    return None


def _contains_blocked_interpreter(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return True

    segment_start = True
    skip_env_assignments = False
    for token in tokens:
        if token in _SHELL_CHAIN_TOKENS:
            segment_start = True
            skip_env_assignments = False
            continue
        if not segment_start:
            continue
        if token == "env":
            skip_env_assignments = True
            continue
        if skip_env_assignments and "=" in token and not token.startswith(("/", "./", "../")):
            continue
        candidate = Path(token).name.lower()
        if candidate in _BLOCKED_INTERPRETER_NAMES:
            return True
        segment_start = False
        skip_env_assignments = False
    return False


def _quota_error_for_workspace(workspace_root: Path, limits: WebDemoLimits) -> str | None:
    usage = workspace_usage(workspace_root)
    if usage.file_count > limits.max_workspace_files:
        return "Sandbox file count limit exceeded."
    if usage.total_bytes > limits.max_workspace_bytes:
        return "Sandbox storage limit exceeded."
    return None


def _restore_workspace_baseline(workspace_root: Path) -> None:
    subprocess.run(
        ["git", "reset", "--hard", "HEAD"],
        cwd=workspace_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(
        ["git", "clean", "-fdx"],
        cwd=workspace_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
