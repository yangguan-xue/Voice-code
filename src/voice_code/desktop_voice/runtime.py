"""Runtime wiring for the desktop voice companion bridge."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI

from voice_code.desktop.permissions import DesktopPermissionApprover
from voice_code.desktop_voice.bridge import DesktopVoiceBridge
from voice_code.desktop_voice.server import DesktopVoiceServer
from voice_code.goals.workspace import GoalWorkspaceManager
from voice_code.llm.models import init_model
from voice_code.permissions import PermissionContext, load_workspace_rules
from voice_code.voice.agent_bridge import AgentBridge
from voice_code.voice.audio_player import AudioPlayer
from voice_code.voice.stepfun_client import StepFunASRClient, StepFunTTSClient
from voice_code.voice.stt_client import SttClient
from voice_code.voice.tts_client import VoxcTtsClient
from voice_code.voice.types import SupportsSttClient, SupportsTtsClient


@dataclass(slots=True)
class DesktopVoiceRuntime:
    bridge: DesktopVoiceBridge
    server: DesktopVoiceServer
    permission_approver: DesktopPermissionApprover
    token: str
    session_id: str
    cwd: str
    model: str
    backend: str


async def create_desktop_voice_runtime(
    *,
    profile: str | None = None,
    workspace: str | None = None,
    token: str | None = None,
    stt_url: str = "http://localhost:8765",
    tts_url: str = "http://localhost:8775",
    stepfun_key: str = "",
    stepfun_voice: str = "cixingnansheng",
    tts_muted: bool = True,
    api_key: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
    custom_profile_id: str | None = None,
) -> DesktopVoiceRuntime:
    loop = asyncio.get_running_loop()
    summary_model: ChatOpenAI = init_model(
        profile=profile,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        temperature=0.0,
        timeout=10.0,
    )
    bridge_ref: dict[str, DesktopVoiceBridge] = {}

    def emit_permission_event(event) -> None:
        bridge = bridge_ref.get("bridge")
        if bridge is None:
            return
        asyncio.run_coroutine_threadsafe(bridge.emit_event(event), loop)

    permission_approver = DesktopPermissionApprover(
        emit=emit_permission_event,
    )
    permission_context = PermissionContext(
        mode="default",
        approver=permission_approver,
        agent_type="desktop-voice",
    )
    agent_bridge = AgentBridge(
        profile=profile,
        summary_model=summary_model,
        workspace=workspace,
        permission_context=permission_context,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        active_profile_name=custom_profile_id,
    )

    async def create_parallel_agent(workspace_path: str) -> AgentBridge:
        parallel_permission_context = PermissionContext(
            mode="default",
            approver=permission_approver,
            agent_type="desktop-voice-worktree",
        )
        parallel_agent_bridge = AgentBridge(
            profile=profile,
            summary_model=summary_model,
            workspace=workspace_path,
            permission_context=parallel_permission_context,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            active_profile_name=custom_profile_id,
        )
        await parallel_agent_bridge.start()
        if parallel_agent_bridge._runtime is None:  # pragma: no cover - defensive
            raise RuntimeError("parallel voice agent bridge failed to start")
        parallel_permission_context.session_id = parallel_agent_bridge._runtime.session_id
        parallel_permission_context.workspace_root = parallel_agent_bridge._runtime.cwd
        parallel_permission_context.workspace_rules = load_workspace_rules(
            parallel_agent_bridge._runtime.cwd
        )
        return parallel_agent_bridge

    if stepfun_key:
        stt_client: SupportsSttClient = StepFunASRClient(api_key=stepfun_key)
        tts_client: SupportsTtsClient = StepFunTTSClient(
            api_key=stepfun_key,
            voice=stepfun_voice,
        )
        backend = "Step Fun"
    else:
        stt_client = SttClient(base_url=f"{stt_url.rstrip('/')}/transcribe")
        tts_client = VoxcTtsClient(base_url=f"{tts_url.rstrip('/')}/tts")
        backend = "自建语音服务"

    resolved_token = token or secrets.token_urlsafe(32)
    bridge = DesktopVoiceBridge(
        session_id="pending",
        agent_bridge=agent_bridge,
        stt_client=stt_client,
        tts_client=tts_client,
        audio_player=AudioPlayer(),
        worktree_manager=GoalWorkspaceManager(workspace or "."),
        parallel_agent_factory=create_parallel_agent,
    )
    bridge_ref["bridge"] = bridge
    bridge.set_tts_muted(tts_muted)
    await bridge.start()

    if agent_bridge._runtime is None:  # pragma: no cover - defensive
        raise RuntimeError("voice agent bridge failed to start")

    bridge.session_id = agent_bridge._runtime.session_id
    permission_context.session_id = agent_bridge._runtime.session_id
    permission_context.workspace_root = agent_bridge._runtime.cwd
    permission_context.workspace_rules = load_workspace_rules(agent_bridge._runtime.cwd)
    server = DesktopVoiceServer(
        bridge=bridge,
        token=resolved_token,
        permission_approver=permission_approver,
    )

    return DesktopVoiceRuntime(
        bridge=bridge,
        server=server,
        permission_approver=permission_approver,
        token=resolved_token,
        session_id=agent_bridge._runtime.session_id,
        cwd=str(agent_bridge._runtime.cwd),
        model=agent_bridge._runtime.model.model_name,
        backend=backend,
    )


def runtime_payload(bundle: DesktopVoiceRuntime, *, url: str) -> dict[str, Any]:
    return {
        "url": url,
        "token": bundle.token,
        "sessionId": bundle.session_id,
        "cwd": bundle.cwd,
        "model": bundle.model,
        "backend": bundle.backend,
        "ttsMuted": bundle.bridge.tts_muted,
    }
