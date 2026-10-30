"""Tenant-scoped session runtime manager for the web demo.

与旧 DemoSessionManager 的本质区别：
- 会话是**持久**的（transcript + sandbox 常驻磁盘，不再 30 分钟过期删除）；
- 热 runtime（LLM 上下文/工具）**懒加载 + 空闲回收**，回收后下条消息到来时
  用 transcript 冷启动重建并注入 resume_messages，用户无感知；
- 一切资源按租户隔离：sandbox 在 <sandbox_root>/tenants/<tenant_id>/workspace，
  transcript 在 <data_root>/tenants/<tenant_id>/transcripts/。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
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
from voice_code.session import TranscriptReader, make_session_id
from voice_code.web_demo.docker_sandbox import create_web_demo_bash_tool
from voice_code.web_demo.limits import WebDemoLimits
from voice_code.web_demo.permissions import WebPermissionApprover
from voice_code.web_demo.protocol import WebDemoEvent
from voice_code.web_demo.sandbox import (
    DemoSandbox,
    SandboxDiff,
    SandboxFile,
    SandboxManager,
    workspace_usage,
)
from voice_code.web_demo.tenants import (
    ConversationRecord,
    Tenant,
    TenantAuthError,
    TenantStore,
)

RunnerFactory = Callable[
    ["TenantSession", WebPermissionApprover],
    AgentTurnRunner | Awaitable[AgentTurnRunner],
]

_WEB_DEMO_EXCLUDED_SKILLS = frozenset({"gateway-image"})

_WEB_DEMO_ALLOWED_TOOL_NAMES = frozenset(
    {"read", "write", "edit", "glob", "grep", "todo_write", "image_gen", "image_edit"}
)

# TenantAuthError 的 code → 对外结构化错误码（传输层据此映射状态码）
_AUTH_ERROR_CODES = {
    "INVALID_ACCESS_CODE": "UNAUTHORIZED",
    "INVALID_TOKEN": "UNAUTHORIZED",
    "TENANT_DISABLED": "TENANT_DISABLED",
}


class WebDemoSessionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class _TenantRuntimeState:
    """热 runtime：空闲可回收；transcript 是上下文的唯一事实来源。"""

    runtime: RuntimeBootstrap
    runner: AgentTurnRunner


@dataclass(slots=True, eq=False)
class TenantSession:
    """租户的一个持久会话（conversation）。"""

    tenant_id: str
    tenant_label: str
    conversation_id: str
    title: str
    sandbox: DemoSandbox
    transcript_path: Path
    loop: asyncio.AbstractEventLoop
    permission_approver: WebPermissionApprover | None = None
    bridge: DesktopAgentBridge | None = None
    model_name: str = "configured model"
    permission_mode: str = "default"
    runtime_state: _TenantRuntimeState | None = None
    runtime_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_active_monotonic: float = field(default_factory=time.monotonic)
    _emit: BridgeEmitter | None = field(default=None, init=False, repr=False)

    @property
    def sandbox_path(self) -> Path:
        return self.sandbox.path

    @property
    def workspace_label(self) -> str:
        return self.sandbox.workspace_label

    def touch(self) -> None:
        self.last_active_monotonic = time.monotonic()

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
            # sessionId 沿用旧字段名，语义已换成 conversation_id
            "sessionId": self.conversation_id,
            "conversationId": self.conversation_id,
            "tenantId": self.tenant_id,
            "tenantLabel": self.tenant_label,
            "title": self.title,
            "workspaceLabel": self.workspace_label,
            "permissionMode": self.permission_mode,
            "model": self.model_name,
        }

class TenantSessionManager:
    """Owns tenant auth, conversation lifecycle, sandbox/runtime isolation."""

    def __init__(
        self,
        *,
        tenant_store: TenantStore,
        sandbox_root: str | Path,
        data_root: str | Path,
        template_repo: str | Path | None = None,
        runner_factory: RunnerFactory | None = None,
        limits: WebDemoLimits | None = None,
        profile: str | None = None,
        model_name: str | None = None,
    ) -> None:
        self._store = tenant_store
        self._sandbox_root = Path(sandbox_root)
        self._data_root = Path(data_root)
        self._template_repo = Path(template_repo) if template_repo else None
        self._runner_factory = runner_factory
        self._limits = limits or WebDemoLimits()
        self._profile = profile
        self._model_name = model_name
        self._lock = asyncio.Lock()
        self._sessions: dict[tuple[str, str], TenantSession] = {}
        self._tenant_sandbox_managers: dict[str, SandboxManager] = {}
        self._login_attempts_by_client: dict[str, list[float]] = {}
        self._recycle_task: asyncio.Task[None] | None = None

    # --- 鉴权（代理 TenantStore，统一错误码） ---

    def login(
        self, access_code: str, *, client_id: str = "anonymous"
    ) -> tuple[Tenant, str, datetime]:
        """访问码登录 → (tenant, 明文 token, 过期时间)。明文 token 只在此处返回一次。"""
        self._check_login_rate_limit(client_id)
        try:
            tenant = self._store.verify_access_code(access_code)
        except TenantAuthError as exc:
            raise WebDemoSessionError(_auth_code(exc.code), exc.message) from exc
        raw_token, expires_at = self._store.issue_token(tenant.tenant_id)
        return tenant, raw_token, expires_at

    def verify_token(self, raw_token: str) -> Tenant:
        """校验 token（滑动续期在 store 内完成）。WS/REST 各入口统一走这里。"""
        try:
            return self._store.verify_token(raw_token)
        except TenantAuthError as exc:
            raise WebDemoSessionError(_auth_code(exc.code), exc.message) from exc

    def logout(self, raw_token: str) -> None:
        if raw_token:
            self._store.revoke_token(raw_token)

    # --- 会话 ---

    async def attach(self, raw_token: str, *, client_id: str = "anonymous") -> TenantSession:
        """tenant.session.attach：定位租户当前活跃会话（没有则新建）。幂等。"""
        tenant = self.verify_token(raw_token)
        conversation = self._store.get_current_conversation(tenant.tenant_id)
        if conversation is None:
            conversation = self._create_conversation_record(tenant.tenant_id)
        return await self._get_or_load_session(tenant, conversation)

    async def get_session(self, raw_token: str) -> TenantSession:
        """WS 各 method 的统一入口：tenantToken → 当前活跃会话。"""
        return await self.attach(raw_token)

    async def start_new_conversation(self, raw_token: str) -> TenantSession:
        tenant = self.verify_token(raw_token)
        conversation = self._create_conversation_record(tenant.tenant_id)
        return await self._get_or_load_session(tenant, conversation)

    async def switch_conversation(self, raw_token: str, conversation_id: str) -> TenantSession:
        tenant = self.verify_token(raw_token)
        try:
            record = self._store.get_conversation(tenant.tenant_id, conversation_id)
            self._store.set_current_conversation(tenant.tenant_id, conversation_id)
        except TenantAuthError as exc:
            raise WebDemoSessionError(exc.code, exc.message) from exc
        return await self._get_or_load_session(tenant, record)

    def list_conversations(
        self, raw_token: str
    ) -> tuple[Tenant, list[ConversationRecord], str | None]:
        tenant = self.verify_token(raw_token)
        conversations = self._store.list_conversations(tenant.tenant_id)
        current = self._store.get_current_conversation(tenant.tenant_id)
        return tenant, conversations, current.conversation_id if current else None

    def get_conversation_record(self, raw_token: str, conversation_id: str) -> ConversationRecord:
        tenant = self.verify_token(raw_token)
        try:
            return self._store.get_conversation(tenant.tenant_id, conversation_id)
        except TenantAuthError as exc:
            raise WebDemoSessionError(exc.code, exc.message) from exc

    def resolve_transcript_path(self, tenant_id: str, conversation: ConversationRecord) -> Path:
        """transcript 相对路径 → 绝对路径，强制落在该租户目录内。"""
        tenant_dir = (self._data_root / "tenants" / tenant_id).resolve(strict=False)
        path = (self._data_root / conversation.transcript_path).resolve(strict=False)
        try:
            path.relative_to(tenant_dir)
        except ValueError as exc:
            raise WebDemoSessionError("CONVERSATION_NOT_FOUND", "Conversation not found.") from exc
        return path

    def tenant_workspace_path(self, tenant_id: str) -> Path:
        """租户工作区路径（历史回放脱敏等场景用，不要求目录已存在）。"""
        return (self._sandbox_root / "tenants" / tenant_id / "workspace").resolve(strict=False)

    async def discard(self, session: TenantSession) -> TenantSession:
        """session.discard：归档当前会话（transcript 保留）并开新会话。"""
        tenant = self._store.get_tenant(session.tenant_id)
        if tenant is None:
            raise WebDemoSessionError("UNAUTHORIZED", "Tenant no longer exists.")
        self._store.archive_conversation(session.tenant_id, session.conversation_id)
        if session.permission_approver is not None:
            session.permission_approver.deny_all_pending("Conversation discarded.")
        self._drop_runtime(session)
        async with self._lock:
            self._sessions.pop((session.tenant_id, session.conversation_id), None)
        conversation = self._create_conversation_record(session.tenant_id)
        return await self._get_or_load_session(tenant, conversation)

    def maybe_set_title(self, session: TenantSession, text: str) -> None:
        """首条用户消息生成会话标题（空标题才写，后续轮不覆盖）。"""
        record = self._store.get_conversation(session.tenant_id, session.conversation_id)
        if record.title:
            return
        title = " ".join(text.strip().split())[:40]
        if not title:
            return
        self._store.update_conversation_title(session.tenant_id, session.conversation_id, title)
        session.title = title

    # --- 沙箱操作（语义与旧版一致，只是 sandbox 变成租户级持久目录） ---

    async def diff(self, session: TenantSession) -> SandboxDiff:
        return await self._sandbox_manager(session.tenant_id).diff(session.sandbox)

    async def read_file(self, session: TenantSession, relative_path: str) -> SandboxFile:
        self.ensure_workspace_within_limits(session)
        return await self._sandbox_manager(session.tenant_id).read_file(
            session.sandbox, relative_path
        )

    async def write_file(self, session: TenantSession, relative_path: str, data: bytes) -> str:
        written = await self._sandbox_manager(session.tenant_id).write_file(
            session.sandbox,
            relative_path,
            data,
            max_bytes=self._limits.max_upload_bytes,
        )
        self.ensure_workspace_within_limits(session)
        return written

    async def reset(self, session: TenantSession) -> None:
        """工作区重置为模板。热 runtime 的 prompt/工具状态随之过期，一并回收。"""
        manager = self._sandbox_manager(session.tenant_id)
        session.sandbox = await manager.reset(session.sandbox)
        self._drop_runtime(session)
        # approver 持有旧 sandbox 引用，重建
        session.permission_approver = WebPermissionApprover(
            emit=session.emit_event_threadsafe,
            sandbox=session.sandbox,
            session_id=session.conversation_id,
            timeout_seconds=self._limits.permission_timeout_seconds,
        )

    def ensure_workspace_within_limits(self, session: TenantSession) -> None:
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

    # --- 热 runtime：懒加载 / 空闲回收 / 容量驱逐 ---

    async def ensure_runtime(self, session: TenantSession) -> _TenantRuntimeState:
        """会话级锁防并发重建；重建且 transcript 非空时通知前端"已恢复上下文"。"""
        recycled = False
        async with session.runtime_lock:
            if session.runtime_state is None:
                recycled = (
                    session.transcript_path.exists()
                    and session.transcript_path.stat().st_size > 0
                )
                session.runtime_state = await self._build_runtime_state(session)
                session.model_name = session.runtime_state.runtime.model.model_name
                self._evict_runtime_capacity()
            session.touch()
            state = session.runtime_state
        if recycled:
            await session.emit_event(
                WebDemoEvent(
                    event="runtime.recycled",
                    sessionId=session.conversation_id,
                    payload={"conversationId": session.conversation_id},
                )
            )
        return state

    async def recycle_idle_runtimes(self, *, now: float | None = None) -> int:
        """回收空闲热 runtime：只卸内存，transcript/sandbox 不动。"""
        current = now if now is not None else time.monotonic()
        recycled = 0
        for session in list(self._sessions.values()):
            if session.runtime_state is None:
                continue
            if session.bridge is not None and session.bridge.is_busy:
                continue  # 回合进行中绝不回收
            if current - session.last_active_monotonic < self._limits.runtime_idle_timeout_seconds:
                continue
            if session.permission_approver is not None:
                session.permission_approver.deny_all_pending("Runtime recycled after inactivity.")
            self._drop_runtime(session)
            recycled += 1
        return recycled

    def _drop_runtime(self, session: TenantSession) -> None:
        state = session.runtime_state
        session.runtime_state = None
        if state is None:
            return
        try:
            state.runtime.transcript_writer.close()
        except OSError:
            pass

    def _evict_runtime_capacity(self) -> None:
        active = [s for s in self._sessions.values() if s.runtime_state is not None]
        overflow = len(active) - self._limits.max_active_runtimes
        if overflow <= 0:
            return
        candidates = sorted(
            (s for s in active if not (s.bridge is not None and s.bridge.is_busy)),
            key=lambda s: s.last_active_monotonic,
        )
        for victim in candidates[:overflow]:
            self._drop_runtime(victim)

    def _ensure_recycle_task(self) -> None:
        if self._recycle_task is None or self._recycle_task.done():
            self._recycle_task = asyncio.create_task(self._recycle_loop())

    async def _recycle_loop(self) -> None:
        while True:
            await asyncio.sleep(self._limits.runtime_recycle_interval_seconds)
            try:
                await self.recycle_idle_runtimes()
            except Exception:
                # 后台回收任务绝不能死；下一轮再试
                continue

    async def shutdown(self) -> None:
        """进程退出/测试收尾：停掉后台回收任务并卸载所有热 runtime。"""
        if self._recycle_task is not None:
            self._recycle_task.cancel()
            self._recycle_task = None
        for session in list(self._sessions.values()):
            self._drop_runtime(session)

    # --- 内部：会话装载 / 沙箱 / runtime 构建 ---

    def _create_conversation_record(self, tenant_id: str) -> ConversationRecord:
        conversation_id = make_session_id()
        transcript_rel = f"tenants/{tenant_id}/transcripts/{conversation_id}.jsonl"
        return self._store.create_conversation(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            title="",
            transcript_path=transcript_rel,
        )

    async def _get_or_load_session(
        self, tenant: Tenant, conversation: ConversationRecord
    ) -> TenantSession:
        key = (tenant.tenant_id, conversation.conversation_id)
        async with self._lock:
            existing = self._sessions.get(key)
            if existing is not None:
                existing.touch()
                return existing

        sandbox = await self._ensure_tenant_sandbox(tenant.tenant_id)
        transcript_path = self.resolve_transcript_path(tenant.tenant_id, conversation)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)

        async with self._lock:
            existing = self._sessions.get(key)
            if existing is not None:
                existing.touch()
                return existing
            session = TenantSession(
                tenant_id=tenant.tenant_id,
                tenant_label=tenant.label,
                conversation_id=conversation.conversation_id,
                title=conversation.title,
                sandbox=sandbox,
                transcript_path=transcript_path,
                loop=asyncio.get_running_loop(),
            )
            session.permission_approver = WebPermissionApprover(
                emit=session.emit_event_threadsafe,
                sandbox=session.sandbox,
                session_id=session.conversation_id,
                timeout_seconds=self._limits.permission_timeout_seconds,
            )
            await self._build_bridge(session)
            self._sessions[key] = session
            self._ensure_recycle_task()
            return session

    async def _build_bridge(self, session: TenantSession) -> None:
        assert session.permission_approver is not None
        if self._runner_factory is not None:
            # 测试注入路径：runner 由测试提供，不走懒加载
            runner = self._runner_factory(session, session.permission_approver)
            if inspect.isawaitable(runner):
                runner = await runner
            session.bridge = DesktopAgentBridge(
                session_id=session.conversation_id,
                runner=runner,  # type: ignore[arg-type]
            )
            return

        async def lazy_runner(
            text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
            user_images: list[dict[str, str]] | None = None,
        ):
            # 懒加载的关键：回合开始才构建/重建热 runtime
            state = await self.ensure_runtime(session)
            async for event in state.runner(
                text,
                abort_signal=abort_signal,
                permission_mode=permission_mode,
                user_images=user_images,
            ):
                yield event

        session.bridge = DesktopAgentBridge(
            session_id=session.conversation_id,
            runner=lazy_runner,
        )

    def _sandbox_manager(self, tenant_id: str) -> SandboxManager:
        manager = self._tenant_sandbox_managers.get(tenant_id)
        if manager is None:
            manager = SandboxManager(
                self._sandbox_root / "tenants" / tenant_id,
                template_repo=self._template_repo,
            )
            self._tenant_sandbox_managers[tenant_id] = manager
        return manager

    async def _ensure_tenant_sandbox(self, tenant_id: str) -> DemoSandbox:
        """租户工作区 get-or-create：目录在就直接复用（持久），不在才从模板创建。"""
        manager = self._sandbox_manager(tenant_id)
        target = manager.root / "workspace"
        if target.is_dir() and not target.is_symlink():
            return DemoSandbox(
                session_id="workspace",
                path=target,
                # 同包内复用 label 规则，避免再抄一遍模板名逻辑
                workspace_label=manager._workspace_label(target),
            )
        return await manager.create("workspace")

    async def _build_runtime_state(self, session: TenantSession) -> _TenantRuntimeState:
        resume_messages = self._load_resume_messages(session)
        runtime = await _bootstrap_demo_runtime(
            workspace=str(session.sandbox_path),
            profile=self._profile,
            model_name=self._model_name,
            limits=self._limits,
            session_id=session.conversation_id,
            resume_messages=resume_messages,
            transcript_dir=session.transcript_path.parent,
        )
        assert session.permission_approver is not None
        permission_context = PermissionContext(
            mode="default",
            approver=session.permission_approver,
            session_id=session.conversation_id,
            workspace_root=runtime.cwd,
            workspace_rules=load_workspace_rules(runtime.cwd),
            runtime_rules=_demo_runtime_rules(),
        )

        async def base_runner(
            text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
            user_images: list[dict[str, str]] | None = None,
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
                        llm_timeout_seconds=self._limits.turn_timeout_seconds,
                        user_images=user_images,
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
            user_images: list[dict[str, str]] | None = None,
        ):
            extra: dict[str, object] = {}
            if user_images:
                extra["user_images"] = user_images
            async for event in run_turn_in_worker_thread(
                base_runner,
                text,
                abort_signal=abort_signal,
                permission_mode=permission_mode,
                **extra,
            ):
                yield event

        return _TenantRuntimeState(runtime=runtime, runner=threaded_runner)

    @staticmethod
    def _load_resume_messages(session: TenantSession) -> list[BaseMessage] | None:
        if not session.transcript_path.exists():
            return None
        try:
            messages = TranscriptReader(session.transcript_path).read_all()
        except (OSError, ValueError):
            # 损坏的 transcript 不阻塞使用：下文重新开始，文件仍在盘上可人工恢复
            return None
        return messages or None

    def _check_login_rate_limit(self, client_id: str) -> None:
        window_seconds = 60.0
        now = time.monotonic()
        attempts = [
            attempt
            for attempt in self._login_attempts_by_client.get(client_id, [])
            if now - attempt < window_seconds
        ]
        if len(attempts) >= self._limits.max_session_creates_per_minute:
            self._login_attempts_by_client[client_id] = attempts
            raise WebDemoSessionError(
                "RATE_LIMITED",
                "Too many login attempts. Please wait a minute and try again.",
            )
        attempts.append(now)
        self._login_attempts_by_client[client_id] = attempts


def _auth_code(store_code: str) -> str:
    return _AUTH_ERROR_CODES.get(store_code, "UNAUTHORIZED")

async def _bootstrap_demo_runtime(
    *,
    workspace: str,
    profile: str | None,
    model_name: str | None,
    limits: WebDemoLimits,
    session_id: str | None = None,
    resume_messages: list[BaseMessage] | None = None,
    transcript_dir: Path | None = None,
) -> RuntimeBootstrap:
    runtime = await bootstrap_runtime(
        profile=profile,
        model_name=model_name,
        workspace=workspace,
        session_id=session_id,
        resume_messages=resume_messages,
        transcript_dir=transcript_dir,
    )
    runtime.tools = _build_web_demo_tools(runtime.tools, workspace=runtime.cwd, limits=limits)
    runtime.prompt = await build_workspace_prompt(
        workspace=runtime.cwd,
        tools=runtime.tools,
        model_name=runtime.model.model_name,
        use_cache=False,
        # gateway-image 这类 skill 假设本地 shell 能直接跑脚本；web demo 的
        # bash 在断网 Docker 里、脚本路径又在沙盒外，注入只会诱导 agent 白跑。
        # 沙盒里 image_gen/image_edit 原生工具的描述是唯一权威。
        exclude_skills=_WEB_DEMO_EXCLUDED_SKILLS,
    )
    return runtime


def normalize_permission_mode(permission_mode: str | None) -> str:
    normalized = (permission_mode or "default").strip()
    if normalized in {"default", "dontAsk", "fullAuto"}:
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

    def __getattr__(self, item: str) -> Any:
        # 包装层只覆盖限额逻辑，其余属性（args_schema 等）全部透传给被包装工具，
        # 否则工具 schema 会退化成空 properties。
        if item == "_tool":
            raise AttributeError(item)
        return getattr(self._tool, item)

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
            reason_message="Subagents are disabled in the web demo.",
            risk_category="medium",
            tool_names=("agent",),
        ),
        PermissionRule(
            name="web_demo_no_web_fetch",
            behavior=PermissionBehavior.DENY,
            source=PermissionRuleSource.RUNTIME,
            reason_type=PermissionReasonType.RULE_MATCH,
            reason_message="Network fetches are disabled in the web demo.",
            risk_category="medium",
            tool_names=("web_fetch",),
        ),
    ]
