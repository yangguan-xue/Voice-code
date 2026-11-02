"""Runtime wiring for the desktop voice companion bridge."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from langchain_openai import ChatOpenAI

from voice_code.desktop.permissions import DesktopPermissionApprover
from voice_code.desktop_voice.bridge import DesktopVoiceBridge
from voice_code.desktop_voice.server import DesktopVoiceServer
from voice_code.goals.workspace import GoalWorkspaceManager
from voice_code.llm.models import init_model
from voice_code.permissions import PermissionContext, load_workspace_rules
from voice_code.voice.agent_bridge import AgentBridge
from voice_code.voice.audio_player import AudioPlayer
from voice_code.voice.stepfun_client import DEFAULT_VOICE, StepFunASRClient, StepFunTTSClient
from voice_code.voice.stt_client import SttClient
from voice_code.voice.tts_client import VoxcTtsClient
from voice_code.voice.types import SupportsSttClient, SupportsTtsClient

REMOTE_VOICE_PROVIDER = "remote"
STEPFUN_VOICE_PROVIDER = "stepfun"
DEFAULT_REMOTE_STT_URL = "http://localhost:8765"
DEFAULT_REMOTE_TTS_URL = "http://localhost:8775"


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
    voice_provider: str | None = None,
    stt_url: str = DEFAULT_REMOTE_STT_URL,
    tts_url: str = DEFAULT_REMOTE_TTS_URL,
    stepfun_key: str = "",
    stepfun_voice: str = DEFAULT_VOICE,
    stepfun_base_url: str = "",
    voice_api_key: str = "",
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

    stt_client, tts_client, backend = create_voice_clients(
        voice_provider=voice_provider,
        stt_url=stt_url,
        tts_url=tts_url,
        stepfun_key=stepfun_key,
        stepfun_voice=stepfun_voice,
        stepfun_base_url=stepfun_base_url,
        voice_api_key=voice_api_key,
    )

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


def create_voice_clients(
    *,
    voice_provider: str | None = None,
    stt_url: str = DEFAULT_REMOTE_STT_URL,
    tts_url: str = DEFAULT_REMOTE_TTS_URL,
    stepfun_key: str = "",
    stepfun_voice: str = DEFAULT_VOICE,
    stepfun_base_url: str = "",
    voice_api_key: str = "",
) -> tuple[SupportsSttClient, SupportsTtsClient, str]:
    provider = _resolve_voice_provider(voice_provider, stepfun_key=stepfun_key)
    if provider == STEPFUN_VOICE_PROVIDER:
        if not stepfun_key.strip():
            raise ValueError("Step Fun voice provider requires an API key.")
        return (
            StepFunASRClient(
                api_key=stepfun_key.strip(),
                base_url=stepfun_base_url.strip() or None,
            ),
            StepFunTTSClient(
                api_key=stepfun_key.strip(),
                voice=stepfun_voice.strip() or DEFAULT_VOICE,
                base_url=stepfun_base_url.strip() or None,
            ),
            "Step Fun",
        )

    if provider == REMOTE_VOICE_PROVIDER:
        return (
            SttClient(
                base_url=_endpoint_url(stt_url, "transcribe"),
                api_key=voice_api_key,
            ),
            VoxcTtsClient(
                base_url=_endpoint_url(tts_url, "tts"),
                api_key=voice_api_key,
            ),
            "自建语音服务",
        )

    raise ValueError(f"Unknown voice provider: {provider}")


def _resolve_voice_provider(provider: str | None, *, stepfun_key: str) -> str:
    normalized = (provider or "").strip().lower()
    if not normalized:
        return STEPFUN_VOICE_PROVIDER if stepfun_key.strip() else REMOTE_VOICE_PROVIDER
    if normalized in {"remote", "remote_url", "self_hosted", "local"}:
        return REMOTE_VOICE_PROVIDER
    if normalized in {"stepfun", "step_fun", "step-fun"}:
        return STEPFUN_VOICE_PROVIDER
    return normalized


def _endpoint_url(base_url: str, endpoint: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_REMOTE_STT_URL if endpoint == "transcribe" else DEFAULT_REMOTE_TTS_URL
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    if path.rsplit("/", 1)[-1] != endpoint:
        path = f"{path}/{endpoint}" if path else f"/{endpoint}"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
