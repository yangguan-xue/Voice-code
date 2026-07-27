"""Session runtime manager for the web demo sandbox agent."""

from __future__ import annotations

import asyncio
import inspect
import secrets
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage

from voice_code.agent.abort import AbortSignal
from voice_code.agent.loop import agent_loop
from voice_code.desktop.bridge import AgentTurnRunner, BridgeEmitter, DesktopAgentBridge
from voice_code.desktop.runner import run_turn_in_worker_thread
from voice_code.permissions import (
    PermissionBehavior,
    PermissionContext,
    PermissionReasonType,
    PermissionRule,
    PermissionRuleSource,
    load_workspace_rules,
)
from voice_code.runtime import RuntimeBootstrap, bootstrap_runtime, build_workspace_prompt
from voice_code.security import workspace_boundary
from voice_code.web_demo.docker_sandbox import create_web_demo_bash_tool
from voice_code.web_demo.limits import WebDemoLimits
from voice_code.web_demo.permissions import WebPermissionApprover
from voice_code.web_demo.protocol import WebDemoEvent
from voice_code.web_demo.sandbox import (
    DemoSandbox,
    SandboxBoundaryError,
    SandboxDiff,
    SandboxFile,
    SandboxManager,
    workspace_usage,
)

RunnerFactory = Callable[
    ["DemoSession", WebPermissionApprover],
    AgentTurnRunner | Awaitable[AgentTurnRunner],
]

_WEB_DEMO_ALLOWED_TOOL_NAMES = frozenset({"read", "write", "edit", "glob", "grep", "todo_write"})


class WebDemoSessionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WebDemoLimitError(RuntimeError):
    pass


@dataclass(slots=True, eq=False)
class DemoSession:
    session_id: str
    session_token: str
    sandbox: DemoSandbox
    expires_at: datetime
    loop: asyncio.AbstractEventLoop
    permission_approver: WebPermissionApprover | None = None
    bridge: DesktopAgentBridge | None = None
    model_name: str = "configured model"
    permission_mode: str = "default"
    _emit: BridgeEmitter | None = field(default=None, init=False, repr=False)

    @property
    def sandbox_path(self) -> Path:
        return self.sandbox.path

    @property
    def workspace_label(self) -> str:
        return self.sandbox.workspace_label

    def is_expired(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        return current >= self.expires_at

    def set_emit(self, emit: BridgeEmitter | None) -> BridgeEmitter | None:
        previous = self._emit
        self._emit = emit
        if self.bridge is not None:
            self.bridge.set_emit(emit)
        return previous

    async def emit_event(self, event: WebDemoEvent) -> None:
        if self._emit is None:
            return
        result = self._emit(event)  # type: ignore[arg-type]
        if result is not None:
            await result

    def emit_event_threadsafe(self, event: WebDemoEvent) -> None:
        asyncio.run_coroutine_threadsafe(self.emit_event(event), self.loop)

    def to_payload(self) -> dict[str, object]:
        return {
            "sessionId": self.session_id,
            "sessionToken": self.session_token,
            "expiresAt": self.expires_at.isoformat().replace("+00:00", "Z"),
            "workspaceLabel": self.workspace_label,
            "permissionMode": self.permission_mode,
            "model": self.model_name,
        }


class DemoSessionManager:
    """Owns demo session lifecycle and sandbox/runtime isolation."""

    def __init__(
        self,
        *,
        access_code: str,
        sandbox_root: str | Path,
        template_repo: str | Path | None = None,
        runner_factory: RunnerFactory | None = None,
        limits: WebDemoLimits | None = None,
        profile: str | None = None,
        model_name: str | None = None,
    ) -> None:
        if not access_code:
            raise ValueError("web demo access code must not be empty")
        self._access_code = access_code
        self._sandbox_manager = SandboxManager(sandbox_root, template_repo=template_repo)
        self._runner_factory = runner_factory
        self._limits = limits or WebDemoLimits()
        self._profile = profile
        self._model_name = model_name
        self._lock = asyncio.Lock()
        self._sessions_by_token: dict[str, DemoSession] = {}
        self._sessions_by_id: dict[str, DemoSession] = {}
        self._create_attempts_by_client: dict[str, list[float]] = {}

    async def create_session(
        self,
        access_code: str,
        *,
        client_id: str = "anonymous",
    ) -> DemoSession:
        if not secrets.compare_digest(access_code, self._access_code):
            raise WebDemoSessionError("UNAUTHORIZED", "Invalid demo access code.")

        async with self._lock:
            self._check_create_rate_limit(client_id)
            await self.cleanup_expired()
            if len(self._sessions_by_token) >= self._limits.max_active_sessions:
                raise WebDemoSessionError("CAPACITY_EXCEEDED", "Demo capacity is full.")

            session_id = f"demo_{secrets.token_hex(8)}"
            session_token = secrets.token_urlsafe(32)
            sandbox = await self._sandbox_manager.create(session_id)
            loop = asyncio.get_running_loop()
            session = DemoSession(
                session_id=session_id,
                session_token=session_token,
                sandbox=sandbox,
                expires_at=datetime.now(UTC)
                + timedelta(seconds=self._limits.session_ttl_seconds),
                loop=loop,
            )
            approver = WebPermissionApprover(
                emit=session.emit_event_threadsafe,
                sandbox=sandbox,
                session_id=session_id,
                timeout_seconds=self._limits.permission_timeout_seconds,
            )
            runner = await self._build_runner(session, approver)
            session.permission_approver = approver
            session.bridge = DesktopAgentBridge(session_id=session_id, runner=runner)
            self._sessions_by_token[session_token] = session
            self._sessions_by_id[session_id] = session
            return session

    async def get_by_token(self, session_token: str) -> DemoSession:
        if not session_token:
            raise WebDemoSessionError("UNAUTHORIZED", "Missing demo session token.")
        async with self._lock:
            session = self._sessions_by_token.get(session_token)
            if session is None:
                raise WebDemoSessionError("UNAUTHORIZED", "Invalid demo session token.")
            if session.is_expired():
                await self._expire_locked(session)
                raise WebDemoSessionError("SESSION_EXPIRED", "Demo session expired.")
            return session

    async def diff(self, session: DemoSession) -> SandboxDiff:
        return await self._sandbox_manager.diff(session.sandbox)

    async def read_file(self, session: DemoSession, relative_path: str) -> SandboxFile:
        self.ensure_workspace_within_limits(session)
        return await self._sandbox_manager.read_file(session.sandbox, relative_path)

    async def reset(self, session: DemoSession) -> None:
        session.sandbox = await self._sandbox_manager.reset(session.sandbox)

    async def discard(self, session: DemoSession) -> None:
        async with self._lock:
            await self._discard_locked(session)

    async def cleanup_expired(self) -> int:
        expired = [
            session for session in self._sessions_by_token.values()
            if session.is_expired()
        ]
        for session in expired:
            await self._expire_locked(session)
        return len(expired)

    def ensure_workspace_within_limits(self, session: DemoSession) -> None:
        usage = workspace_usage(session.sandbox_path)
        if usage.file_count > self._limits.max_workspace_files:
            raise WebDemoSessionError(
                "WORKSPACE_LIMIT_EXCEEDED",
                "Sandbox file count limit exceeded. Please reset the sandbox.",
            )
        if usage.total_bytes > self._limits.max_workspace_bytes:
            raise WebDemoSessionError(
                "WORKSPACE_LIMIT_EXCEEDED",
                "Sandbox storage limit exceeded. Please reset the sandbox.",
            )

    def ensure_download_allowed(self, sandbox_file: SandboxFile) -> None:
        if len(sandbox_file.content.encode("utf-8")) > self._limits.max_download_bytes:
            raise WebDemoSessionError(
                "FILE_TOO_LARGE",
                "Requested file is too large to download from the demo sandbox.",
            )

    def _check_create_rate_limit(self, client_id: str) -> None:
        window_seconds = 60.0
        now = time.monotonic()
        attempts = [
            attempt
            for attempt in self._create_attempts_by_client.get(client_id, [])
            if now - attempt < window_seconds
        ]
        if len(attempts) >= self._limits.max_session_creates_per_minute:
            self._create_attempts_by_client[client_id] = attempts
            raise WebDemoSessionError(
                "RATE_LIMITED",
                "Too many demo session requests. Please wait a minute and try again.",
            )
        attempts.append(now)
        self._create_attempts_by_client[client_id] = attempts

    async def _build_runner(
        self,
        session: DemoSession,
        approver: WebPermissionApprover,
    ) -> AgentTurnRunner:
        if self._runner_factory is not None:
            runner = self._runner_factory(session, approver)
            if inspect.isawaitable(runner):
                return await runner
            return runner
        return await _create_default_runner(
            session,
            approver,
            profile=self._profile,
            model_name=self._model_name,
            limits=self._limits,
        )

    async def _expire_locked(self, session: DemoSession) -> None:
        if session.bridge is not None:
            session.bridge.interrupt_turn()
        if session.permission_approver is not None:
            session.permission_approver.deny_all_pending("Demo session expired.")
        await self._discard_locked(session)

    async def _discard_locked(self, session: DemoSession) -> None:
        self._sessions_by_token.pop(session.session_token, None)
        self._sessions_by_id.pop(session.session_id, None)
        with suppress(FileNotFoundError, SandboxBoundaryError):
            await self._sandbox_manager.discard(session.sandbox)


async def _create_default_runner(
    session: DemoSession,
    approver: WebPermissionApprover,
    *,
    profile: str | None,
    model_name: str | None,
    limits: WebDemoLimits,
) -> AgentTurnRunner:
    runtime = await _bootstrap_demo_runtime(
        workspace=str(session.sandbox_path),
        profile=profile,
        model_name=model_name,
        limits=limits,
    )
    session.model_name = runtime.model.model_name
    permission_context = PermissionContext(
        mode="default",
        approver=approver,
        session_id=session.session_id,
        workspace_root=runtime.cwd,
        workspace_rules=load_workspace_rules(runtime.cwd),
        runtime_rules=_demo_runtime_rules(),
    )

    async def base_runner(
        text: str,
        *,
        abort_signal: AbortSignal,
        permission_mode: str | None = None,
    ):
        previous_mode = permission_context.mode
        permission_context.mode = normalize_permission_mode(permission_mode)
        try:
            with workspace_boundary(runtime.cwd):
                async for event in agent_loop(
                    text,
                    runtime.tools,
                    runtime.prompt,
                    runtime.model,
                    permission_context=permission_context,
                    abort_signal=abort_signal,
                    resume_messages=_refresh_resumed_system_prompt(
                        runtime.resume_messages,
                        runtime.prompt,
                    ),
                    transcript_writer=runtime.transcript_writer,
                    fallback_model=runtime.fallback_model,
                    runtime_session_id=runtime.session_id,
                    memory_service=runtime.memory_service,
                    memory_project_key=runtime.memory_project_key,
                    llm_timeout_seconds=limits.turn_timeout_seconds,
                ):
                    yield event
        finally:
            runtime.resume_messages = runtime.transcript_writer.read_all_messages()
            permission_context.mode = previous_mode

    async def threaded_runner(
        text: str,
        *,
        abort_signal: AbortSignal,
        permission_mode: str | None = None,
    ):
        async for event in run_turn_in_worker_thread(
            base_runner,
            text,
            abort_signal=abort_signal,
            permission_mode=permission_mode,
        ):
            yield event

    return threaded_runner


async def _bootstrap_demo_runtime(
    *,
    workspace: str,
    profile: str | None,
    model_name: str | None,
    limits: WebDemoLimits,
) -> RuntimeBootstrap:
    runtime = await bootstrap_runtime(
        profile=profile,
        model_name=model_name,
        workspace=workspace,
    )
    runtime.tools = _build_web_demo_tools(runtime.tools, workspace=runtime.cwd, limits=limits)
    runtime.prompt = await build_workspace_prompt(
        workspace=runtime.cwd,
        tools=runtime.tools,
        model_name=runtime.model.model_name,
        use_cache=False,
    )
    return runtime


def normalize_permission_mode(permission_mode: str | None) -> str:
    normalized = (permission_mode or "default").strip()
    if normalized in {"default", "dontAsk"}:
        return normalized
    return "default"


def _refresh_resumed_system_prompt(
    messages: list[BaseMessage] | None,
    system_prompt: str,
) -> list[BaseMessage] | None:
    if not messages:
        return messages
    refreshed = list(messages)
    for index, message in enumerate(refreshed):
        if isinstance(message, SystemMessage):
            refreshed[index] = SystemMessage(content=system_prompt)
            return refreshed
    refreshed.insert(0, SystemMessage(content=system_prompt))
    return refreshed


def _build_web_demo_tools(tools: list, *, workspace: str, limits: WebDemoLimits) -> list:
    """Keep the browser demo inside workspace-scoped tools.

    File tools stay local to the demo workspace. Shell execution is only
    re-enabled when a Linux Docker sandbox is available for the workspace.
    """

    sandboxed_bash = create_web_demo_bash_tool(workspace, limits=limits)
    selected: list = []
    for tool in tools:
        tool_name = getattr(tool, "name", "")
        if tool_name == "bash":
            if sandboxed_bash is not None:
                selected.append(sandboxed_bash)
            continue
        if tool_name in _WEB_DEMO_ALLOWED_TOOL_NAMES:
            selected.append(_wrap_web_demo_tool(tool, workspace=workspace, limits=limits))
    return selected


def _wrap_web_demo_tool(tool: Any, *, workspace: str, limits: WebDemoLimits) -> Any:
    tool_name = getattr(tool, "name", "")
    if tool_name not in {"write", "edit"}:
        return tool
    return _WebDemoQuotaTool(tool=tool, workspace=Path(workspace), limits=limits)


class _WebDemoQuotaTool:
    def __init__(self, *, tool: Any, workspace: Path, limits: WebDemoLimits) -> None:
        self._tool = tool
        self._workspace = workspace
        self._limits = limits
        self.name = getattr(tool, "name", "")
        self.description = getattr(tool, "description", "")
        self.metadata = getattr(tool, "metadata", {})

    def invoke(self, args: dict[str, object]) -> str:
        target_path = Path(str(args.get("file_path", "")).strip())
        if not target_path:
            return str(self._tool.invoke(args))
        try:
            candidate_path = (
                target_path if target_path.is_absolute() else self._workspace / target_path
            )
            resolved_path = candidate_path.resolve(strict=False)
        except OSError:
            return str(self._tool.invoke(args))

        existed_before = resolved_path.exists()
        previous_content = ""
        if existed_before and resolved_path.is_file():
            try:
                previous_content = resolved_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                previous_content = ""

        result = str(self._tool.invoke(args))
        if result.startswith("<tool_use_error>"):
            return result

        limit_error = self._quota_error_for_path(resolved_path)
        if limit_error is None:
            return result

        _restore_file_state(
            resolved_path,
            existed_before=existed_before,
            previous_content=previous_content,
        )
        return f"<tool_use_error>Error: {limit_error}</tool_use_error>"

    def _quota_error_for_path(self, path: Path) -> str | None:
        if path.exists() and path.is_file():
            try:
                file_bytes = path.stat().st_size
            except OSError:
                file_bytes = 0
            if file_bytes > self._limits.max_file_bytes:
                return "File exceeds the web demo file size limit."
        usage = workspace_usage(self._workspace)
        if usage.file_count > self._limits.max_workspace_files:
            return "Sandbox file count limit exceeded."
        if usage.total_bytes > self._limits.max_workspace_bytes:
            return "Sandbox storage limit exceeded."
        return None


def _restore_file_state(path: Path, *, existed_before: bool, previous_content: str) -> None:
    try:
        if existed_before:
            path.write_text(previous_content, encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError:
        pass


def _demo_runtime_rules() -> list[PermissionRule]:
    deny_patterns = (
        "*curl*",
        "*wget*",
        "*ssh*",
        "*scp*",
        "*nc *",
        "*netcat*",
        "*git push*",
        "*npm install*",
        "*pnpm install*",
        "*yarn install*",
        "*pip install*",
        "*~/.ssh*",
        "*.env*",
    )
    return [
        PermissionRule(
            name="web_demo_bash_denied_commands",
            behavior=PermissionBehavior.DENY,
            source=PermissionRuleSource.RUNTIME,
            reason_type=PermissionReasonType.RULE_MATCH,
            reason_message="This command is blocked in the web demo sandbox.",
            risk_category="high",
            tool_names=("bash",),
            command_patterns=deny_patterns,
        ),
        PermissionRule(
            name="web_demo_no_nested_agents",
            behavior=PermissionBehavior.DENY,
            source=PermissionRuleSource.RUNTIME,
            reason_type=PermissionReasonType.RULE_MATCH,
            reason_message="Subagents are disabled in the web demo V0.",
            risk_category="medium",
            tool_names=("agent",),
        ),
        PermissionRule(
            name="web_demo_no_web_fetch",
            behavior=PermissionBehavior.DENY,
            source=PermissionRuleSource.RUNTIME,
            reason_type=PermissionReasonType.RULE_MATCH,
            reason_message="Network fetches are disabled in the web demo V0.",
            risk_category="medium",
            tool_names=("web_fetch",),
        ),
    ]
