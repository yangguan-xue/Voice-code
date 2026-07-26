"""Bash tool — execute shell commands"""

from __future__ import annotations

import asyncio
import os
import platform
import signal
from dataclasses import dataclass

from langchain_core.tools import tool

from voice_code.audit import record_audit_event
from voice_code.security import WorkspaceBoundaryError, resolve_workspace_path

try:
    import resource
except ImportError:  # pragma: no cover - Windows import smoke
    resource = None  # type: ignore[assignment]

_MAX_OUTPUT_CHARS = 100_000
_NETWORK_TOKENS = ("curl ", "wget ", "ssh ", "scp ", "nc ", "netcat ", "telnet ")


@dataclass(frozen=True, slots=True)
class BashSandboxPolicy:
    allowlist: tuple[str, ...] = ()
    network: str = "allow"
    cpu_seconds: int | None = None
    memory_mb: int | None = None


def _shell() -> str:
    return os.environ.get("SHELL", "bash")


def _timeout_handler():
    """Fallback timeout — deprecated in favor of asyncio.wait_for."""
    pass


def _normalize_policy(policy: BashSandboxPolicy | dict[str, object] | None) -> BashSandboxPolicy:
    if policy is None:
        return BashSandboxPolicy()
    if isinstance(policy, BashSandboxPolicy):
        return policy
    allowlist = policy.get("allowlist", ())
    normalized_allowlist = (
        tuple(str(item) for item in allowlist) if isinstance(allowlist, list | tuple) else ()
    )
    raw_cpu_seconds = policy.get("cpu_seconds")
    raw_memory_mb = policy.get("memory_mb")
    return BashSandboxPolicy(
        allowlist=normalized_allowlist,
        network=str(policy.get("network", "allow")),
        cpu_seconds=raw_cpu_seconds if isinstance(raw_cpu_seconds, int) else None,
        memory_mb=raw_memory_mb if isinstance(raw_memory_mb, int) else None,
    )


def _preexec_limits(policy: BashSandboxPolicy):
    if platform.system() == "Windows" or resource is None:
        return None

    def apply_limits() -> None:
        if policy.cpu_seconds is not None and policy.cpu_seconds > 0:
            resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds))
        if policy.memory_mb is not None and policy.memory_mb > 0:
            limit = policy.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return apply_limits


def _network_allowed(command: str, policy: BashSandboxPolicy) -> bool:
    if policy.network != "deny":
        return True
    lowered = f" {command.lower()} "
    return not any(token in lowered for token in _NETWORK_TOKENS)


def _has_write_intent(command: str) -> bool:
    lowered = command.lower()
    return any(token in lowered for token in (">", ">>", "tee ", "sed -i", "perl -pi", "cat >"))


async def _run_command(
    command: str, timeout_ms: int, cwd: str | None = None, policy: BashSandboxPolicy | None = None
) -> tuple[str, str, int]:
    """执行命令，返回 (stdout, stderr, returncode)。"""
    shell = _shell()
    timeout_sec = timeout_ms / 1000.0
    active_policy = policy or BashSandboxPolicy()

    process_kwargs = {"cwd": cwd}
    if platform.system() != "Windows":
        process_kwargs["start_new_session"] = True
        process_kwargs["preexec_fn"] = _preexec_limits(active_policy)

    if platform.system() == "Windows":
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **process_kwargs,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            shell, "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **process_kwargs,
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_sec,
        )
    except TimeoutError:
        if platform.system() == "Windows":
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await proc.wait()
        return (
            "",
            f"<tool_use_error>Error: Command timed out after {timeout_ms}ms</tool_use_error>",
            -1,
        )

    out = stdout.decode("utf-8", errors="replace") if stdout else ""
    err = stderr.decode("utf-8", errors="replace") if stderr else ""

    return out, err, proc.returncode or 0


@tool
def bash(
    command: str,
    description: str = "",
    timeout: int = 120000,
    workdir: str = ".",
    policy: BashSandboxPolicy | dict[str, object] | None = None,
) -> str:
    """Execute a bash command in the persistent shell session with optional timeout.

    IMPORTANT: Do NOT use Bash for file discovery, directory trees, or content
    search when dedicated tools are available. Instead, use the appropriate
    dedicated tool as this will provide a much better experience for the user:
      - File search: use Glob (NOT find or ls)
      - Content search: use Grep (NOT grep or rg)
      - Read files: use FileRead (NOT cat/head/tail)
      - Edit files: use FileEdit (NOT sed/awk)
      - Write files: use FileWrite (NOT echo >/cat <<EOF)
      - Communication: output text directly (NOT echo/printf)
    Reserve Bash exclusively for terminal operations (git, npm, docker, etc.)
    If you are unsure, default to the dedicated tool and only fall back to
    Bash if absolutely necessary.

    Before executing:
    - Directory verification: if the command will create new directories or files,
      first check that the parent directory exists
    - Always quote file paths that contain spaces
    - Use workdir parameter instead of `cd && command` patterns

    Args:
        command: The bash command to execute.
        description: Clear, concise description in 5-10 words.
        timeout: Maximum timeout in milliseconds (default 120000).
        workdir: Working directory within the active workspace.
    """
    try:
        resolved_workdir = resolve_workspace_path(workdir, allow_missing=False)
    except (WorkspaceBoundaryError, FileNotFoundError) as exc:
        return f"<tool_use_error>Error: {exc}</tool_use_error>"
    if not resolved_workdir.is_dir():
        return f"<tool_use_error>Error: Workdir is not a directory: {workdir}</tool_use_error>"

    active_policy = _normalize_policy(policy)
    if active_policy.allowlist:
        try:
            if not any(
                resolved_workdir.is_relative_to(resolve_workspace_path(item, allow_missing=False))
                for item in active_policy.allowlist
            ):
                return (
                    "<tool_use_error>Error: Workdir is outside the Bash allowlist"
                    "</tool_use_error>"
                )
        except (WorkspaceBoundaryError, FileNotFoundError) as exc:
            return f"<tool_use_error>Error: {exc}</tool_use_error>"
    if not _network_allowed(command, active_policy):
        return (
            "<tool_use_error>Error: Network access is disabled by Bash sandbox policy"
            "</tool_use_error>"
        )
    if _has_write_intent(command):
        record_audit_event(
            event_type="bash.write",
            actor="agent",
            resource_id="tool:bash",
            outcome="requested",
        )

    out, err, rc = asyncio.run(
        _run_command(command, timeout, cwd=str(resolved_workdir), policy=active_policy)
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

    return result


bash.metadata = {
    "is_readonly": False,
    "is_concurrency_safe": False,
    "max_result_chars": 100_000,
}
