"""Desktop runtime wiring for the local bridge server."""

from __future__ import annotations

import asyncio
import secrets
import subprocess
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

import voice_code.llm.models as model_config
from voice_code.agent.abort import AbortSignal
from voice_code.agent.loop import agent_loop
from voice_code.agent.types import AgentEvent
from voice_code.desktop.bridge import AgentTurnRunner, DesktopAgentBridge
from voice_code.desktop.permissions import DesktopPermissionApprover
from voice_code.desktop.runner import run_turn_in_worker_thread
from voice_code.desktop.server import DesktopBridgeServer
from voice_code.permissions import PermissionContext, load_workspace_rules
from voice_code.runtime import (
    RuntimeBootstrap,
    bootstrap_runtime,
    build_workspace_prompt,
    resolve_fallback_profile,
)
from voice_code.session import get_session_path, make_session_id
from voice_code.session.resume import resume_runtime_session
from voice_code.session.transcript import TranscriptWriter


@dataclass(slots=True)
class DesktopBridgeRuntime:
    runtime: RuntimeBootstrap
    permission_context: PermissionContext
    permission_approver: DesktopPermissionApprover
    bridge: DesktopAgentBridge
    server: DesktopBridgeServer
    token: str


class DesktopRuntimeActions:
    def __init__(
        self,
        *,
        runtime: RuntimeBootstrap,
        bridge: DesktopAgentBridge,
        permission_context: PermissionContext,
        permission_approver: DesktopPermissionApprover,
        profile: str | None,
        custom_model_config: dict[str, str] | None = None,
    ) -> None:
        self.runtime = runtime
        self.bridge = bridge
        self.permission_context = permission_context
        self.permission_approver = permission_approver
        self.custom_model_config = custom_model_config
        self.active_profile = (
            custom_model_config["id"]
            if custom_model_config is not None
            else model_config.resolve_profile_name(profile)
        )

    def bootstrap_context(self) -> dict[str, object]:
        return {
            "workspacePath": self.runtime.cwd,
            **_git_context(self.runtime.cwd),
            "activeProfile": self.active_profile or "",
            "modelProfiles": model_config.list_model_profile_details(),
        }

    async def resume_session(self, session_id: str) -> dict[str, object]:
        if self.bridge.is_busy:
            raise ValueError("Cannot resume a session while a turn is running.")
        resumed = resume_runtime_session(session_id)
        target_cwd = resumed.runtime_state.cwd or self.runtime.cwd
        messages = resumed.messages

        if target_cwd != self.runtime.cwd:
            resumed.transcript_writer.close()
            custom = self.custom_model_config or {}
            replacement = await bootstrap_runtime(
                profile=None if custom else self.active_profile,
                api_key=custom.get("apiKey"),
                base_url=custom.get("baseUrl"),
                model_name=custom.get("modelName"),
                active_profile_name=self.active_profile if custom else None,
                session_id=session_id,
                resume_messages=messages,
                workspace=target_cwd,
            )
            self.runtime.transcript_writer.close()
            for field_name in self.runtime.__dataclass_fields__:
                setattr(self.runtime, field_name, getattr(replacement, field_name))
            self.permission_context.workspace_root = self.runtime.cwd
            self.permission_context.workspace_rules = load_workspace_rules(self.runtime.cwd)
        else:
            self.runtime.transcript_writer.close()
            self.runtime.transcript_writer = resumed.transcript_writer
            self.runtime.resume_messages = messages
            self.runtime.session_id = session_id

        conversation = _messages_to_conversation(session_id, messages)
        self.bridge.switch_session(
            session_id,
            next_turn_id=len(conversation["turns"]) + 1,
        )
        self.permission_context.session_id = session_id
        self.permission_context.session_whitelist.clear()
        self.permission_context.session_rules.clear()
        self.permission_approver.switch_session(session_id)
        return {
            "sessionId": session_id,
            "workspacePath": self.runtime.cwd,
            "conversation": conversation,
            **_git_context(self.runtime.cwd),
        }

    def create_session(self) -> dict[str, object]:
        if self.bridge.is_busy:
            raise ValueError("Cannot create a session while a turn is running.")
        session_id = make_session_id()
        self.runtime.transcript_writer.close()
        self.runtime.transcript_writer = TranscriptWriter(
            get_session_path(session_id),
            session_meta={"cwd": self.runtime.cwd},
        )
        self.runtime.resume_messages = None
        self.runtime.session_id = session_id
        self.bridge.switch_session(session_id)
        self.permission_context.session_id = session_id
        self.permission_context.session_whitelist.clear()
        self.permission_context.session_rules.clear()
        self.permission_approver.switch_session(session_id)
        return {"sessionId": session_id, "workspacePath": self.runtime.cwd}

    async def checkout_branch(self, branch: str) -> dict[str, object]:
        context = _git_context(self.runtime.cwd)
        branches = context["branches"]
        if branch not in branches:
            raise ValueError(f"Unknown local branch: {branch}")
        if context["isDirty"] and context["branch"] != branch:
            raise ValueError("当前工作区有未提交更改，请先提交或暂存后再切换分支。")
        if context["branch"] != branch:
            _run_git(self.runtime.cwd, "switch", branch)
            self.runtime.prompt = await build_workspace_prompt(
                workspace=self.runtime.cwd,
                tools=self.runtime.tools,
                model_name=self.runtime.model.model_name,
                use_cache=False,
            )
        return _git_context(self.runtime.cwd)

    async def select_model_profile(self, profile: str) -> dict[str, object]:
        available = model_config.list_model_profiles()
        if profile not in available:
            raise ValueError(f"Unknown model profile: {profile}")
        model = model_config.init_model(profile=profile)
        fallback_profile = resolve_fallback_profile(profile)
        fallback_model = (
            model_config.init_model(profile=fallback_profile) if fallback_profile else None
        )
        prompt = await build_workspace_prompt(
            workspace=self.runtime.cwd,
            tools=self.runtime.tools,
            model_name=model.model_name,
            use_cache=False,
        )
        self.runtime.model = model
        self.runtime.fallback_model = fallback_model
        self.runtime.fallback_profile = fallback_profile
        self.runtime.prompt = prompt
        self.active_profile = profile
        self.custom_model_config = None
        model_config.set_active_profile(profile)
        return {"profile": profile, "modelName": model.model_name}

    async def configure_model(self, config: dict[str, str]) -> dict[str, object]:
        if self.bridge.is_busy:
            raise ValueError("Cannot change models while a turn is running.")
        model = model_config.init_model(
            api_key=config["apiKey"],
            base_url=config["baseUrl"],
            model_name=config["modelName"],
        )
        prompt = await build_workspace_prompt(
            workspace=self.runtime.cwd,
            tools=self.runtime.tools,
            model_name=model.model_name,
            use_cache=False,
        )
        self.runtime.model = model
        self.runtime.fallback_model = None
        self.runtime.fallback_profile = None
        self.runtime.prompt = prompt
        self.active_profile = config["id"]
        self.custom_model_config = dict(config)
        model_config.set_active_profile(None)
        return {"profile": config["id"], "modelName": model.model_name}


def _messages_to_conversation(
    session_id: str,
    messages: list[BaseMessage],
) -> dict[str, object]:
    turns: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    for message in messages:
        if isinstance(message, HumanMessage):
            current = {
                "id": len(turns) + 1,
                "sessionId": session_id,
                "userText": _message_text(message.content),
                "assistantText": "",
                "reasoningText": "",
                "status": "completed",
                "finishReason": "completed",
                "tools": [],
                "errorText": "",
            }
            turns.append(current)
            continue
        if current is None:
            continue
        if isinstance(message, AIMessage):
            text = _message_text(message.content)
            if text:
                previous = str(current["assistantText"])
                current["assistantText"] = f"{previous}\n\n{text}" if previous else text
            tools = current["tools"]
            if isinstance(tools, list):
                for tool_call in message.tool_calls:
                    tools.append(
                        {
                            "id": str(tool_call.get("id", "")),
                            "name": str(tool_call.get("name", "")),
                            "args": tool_call.get("args", {}),
                            "result": "",
                            "resultPreview": "",
                            "status": "running",
                        }
                    )
            continue
        if isinstance(message, ToolMessage):
            tools = current["tools"]
            if not isinstance(tools, list):
                continue
            result = _message_text(message.content)
            for tool in reversed(tools):
                if tool["id"] == message.tool_call_id:
                    tool["result"] = result
                    tool["resultPreview"] = _preview(result)
                    tool["status"] = "completed"
                    break

    for turn in turns:
        tools = turn["tools"]
        if isinstance(tools, list):
            for tool in tools:
                if tool["status"] == "running":
                    tool["status"] = "completed"
    return {"turns": turns, "activeTurnId": None}


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content or "")


def _preview(text: str, limit: int = 160) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _run_git(workspace: str, *args: str) -> str:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["git", "-C", workspace, *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Git command failed."
        raise ValueError(message)
    return result.stdout.strip()


def _git_context(workspace: str) -> dict[str, object]:
    try:
        branch = _run_git(workspace, "branch", "--show-current")
        raw_branches = _run_git(workspace, "branch", "--format=%(refname:short)")
        status = _run_git(workspace, "status", "--porcelain")
    except ValueError:
        return {"branch": "", "branches": [], "isDirty": False}
    branches = [value.strip() for value in raw_branches.splitlines() if value.strip()]
    return {"branch": branch, "branches": branches, "isDirty": bool(status)}


def make_desktop_agent_runner(
    runtime: RuntimeBootstrap,
    *,
    permission_context: PermissionContext,
) -> AgentTurnRunner:
    """Create the desktop runner around the shared agent loop."""

    async def base_runner(
        text: str,
        *,
        abort_signal: AbortSignal,
        permission_mode: str | None = None,
    ):
        previous_mode = permission_context.mode
        if permission_mode:
            permission_context.mode = permission_mode
        try:
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


async def create_desktop_bridge_runtime(
    *,
    profile: str | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    custom_profile_id: str | None = None,
    workspace: str | None = None,
    token: str | None = None,
) -> DesktopBridgeRuntime:
    """Bootstrap the shared agent runtime and expose it through a desktop bridge."""
    loop = asyncio.get_running_loop()
    runtime = await bootstrap_runtime(
        profile=profile,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        active_profile_name=custom_profile_id,
        workspace=workspace,
    )
    bridge_ref: dict[str, DesktopAgentBridge] = {}

    def emit_permission_event(event) -> None:
        bridge = bridge_ref.get("bridge")
        if bridge is None:
            return
        asyncio.run_coroutine_threadsafe(bridge.emit_event(event), loop)

    permission_approver = DesktopPermissionApprover(
        emit=emit_permission_event,
        session_id=runtime.session_id,
    )
    permission_context = PermissionContext(
        mode="default",
        approver=permission_approver,
        session_id=runtime.session_id,
        workspace_root=runtime.cwd,
        workspace_rules=load_workspace_rules(runtime.cwd),
    )
    runner = make_desktop_agent_runner(runtime, permission_context=permission_context)
    bridge = DesktopAgentBridge(
        session_id=runtime.session_id,
        runner=runner,
    )
    bridge_ref["bridge"] = bridge
    resolved_token = token or secrets.token_urlsafe(32)
    actions = DesktopRuntimeActions(
        runtime=runtime,
        bridge=bridge,
        permission_context=permission_context,
        permission_approver=permission_approver,
        profile=profile,
        custom_model_config=(
            {
                "id": custom_profile_id,
                "displayName": custom_profile_id,
                "baseUrl": base_url,
                "apiKey": api_key,
                "modelName": model_name,
            }
            if custom_profile_id and base_url and api_key and model_name
            else None
        ),
    )
    server = DesktopBridgeServer(
        bridge=bridge,
        token=resolved_token,
        permission_approver=permission_approver,
        actions=actions,
    )

    return DesktopBridgeRuntime(
        runtime=runtime,
        permission_context=permission_context,
        permission_approver=permission_approver,
        bridge=bridge,
        server=server,
        token=resolved_token,
    )


async def collect_events(runner: AgentTurnRunner, text: str) -> list[AgentEvent]:
    """Test helper for runner adapters."""
    events: list[AgentEvent] = []
    async for event in runner(text, abort_signal=AbortSignal()):
        events.append(event)
    return events


def runtime_payload(bundle: DesktopBridgeRuntime, *, url: str) -> dict[str, Any]:
    return {
        "url": url,
        "token": bundle.token,
        "sessionId": bundle.runtime.session_id,
        "cwd": bundle.runtime.cwd,
        "model": bundle.runtime.model.model_name,
    }
