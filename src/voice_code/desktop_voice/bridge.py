"""Voice-safe bridge around the existing voice agent components."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langchain_core.messages import BaseMessage

from voice_code.agent.types import AgentEvent, EventType
from voice_code.desktop.protocol import BridgeEvent
from voice_code.desktop_voice.protocol import (
    VoiceBridgeEvent,
    VoiceEmitter,
    VoiceLaunchMode,
    voice_error_event,
    voice_state_event,
    voice_text_event,
)
from voice_code.goals.workspace import GoalWorkspaceManager
from voice_code.voice.agent_bridge import AgentBridge
from voice_code.voice.audio_player import AudioPlayer
from voice_code.voice.types import SupportsSttClient, SupportsTtsClient

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VoiceTurnResult:
    session_id: str
    turn_id: int
    finish_reason: str
    mode: VoiceLaunchMode = "independentSession"
    context_version: int = 0
    worktree_task_id: str = ""
    worktree_path: str = ""


class DesktopVoiceBridge:
    """Run desktop voice turns while emitting only user-facing voice events."""

    def __init__(
        self,
        *,
        session_id: str,
        agent_bridge: AgentBridge,
        stt_client: SupportsSttClient | None = None,
        tts_client: SupportsTtsClient | None = None,
        audio_player: AudioPlayer | None = None,
        emit: VoiceEmitter | None = None,
        worktree_manager: GoalWorkspaceManager | None = None,
        parallel_agent_factory: Callable[[str], Awaitable[AgentBridge]] | None = None,
    ) -> None:
        self.session_id = session_id
        self._agent_bridge = agent_bridge
        self._stt_client = stt_client
        self._tts_client = tts_client
        self._audio_player = audio_player or AudioPlayer()
        self._emit = emit
        self._worktree_manager = worktree_manager
        self._parallel_agent_factory = parallel_agent_factory
        self._parallel_agents: dict[str, AgentBridge] = {}
        self._parallel_worktree_paths: dict[str, str] = {}
        self._turn_lock = asyncio.Lock()
        self._next_turn_id = 1
        self._active_turn_id: int | None = None
        self._tts_muted = False
        self._assistant_parts: list[str] = []
        self._agent_bridge.on_event(self._on_agent_event)

    @property
    def tts_muted(self) -> bool:
        return self._tts_muted

    async def start(self) -> None:
        await self._agent_bridge.start()

    async def shutdown(self) -> None:
        self._audio_player.stop()
        await self._agent_bridge.shutdown()
        for agent_bridge in self._parallel_agents.values():
            await agent_bridge.shutdown()
        self._parallel_agents.clear()

    def set_emit(self, emit: VoiceEmitter | None) -> VoiceEmitter | None:
        previous = self._emit
        self._emit = emit
        return previous

    async def emit_event(self, event: VoiceBridgeEvent | BridgeEvent) -> None:
        await self._emit_event(event)

    def set_tts_muted(self, muted: bool) -> None:
        self._tts_muted = muted
        if muted:
            self._audio_player.stop()

    async def start_turn(
        self,
        text: str,
        *,
        context_messages: list[BaseMessage] | None = None,
        mode: VoiceLaunchMode = "independentSession",
        context_version: int = 0,
        worktree_task_id: str = "",
    ) -> VoiceTurnResult:
        user_text = text.strip()
        if not user_text:
            raise ValueError("voice turn text must not be empty")

        async with self._turn_lock:
            turn_id = self._next_turn_id
            self._next_turn_id += 1
            self._active_turn_id = turn_id
            self._assistant_parts = []
            agent_bridge = self._agent_bridge
            resolved_worktree_task_id = ""
            resolved_worktree_path = ""
            await self._emit_event(
                voice_text_event(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    event="voice.user.final",
                    text=user_text,
                )
            )
            await self._emit_event(
                voice_state_event(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    state="thinking",
                    text="正在处理",
                )
            )

            finish_reason = "completed"
            try:
                if mode == "parallelWorktree":
                    await self._emit_event(
                        voice_state_event(
                            session_id=self.session_id,
                            turn_id=turn_id,
                            state="thinking",
                            text="正在准备并行 worktree",
                        )
                    )
                    (
                        agent_bridge,
                        resolved_worktree_task_id,
                        resolved_worktree_path,
                    ) = await self._parallel_agent(worktree_task_id)

                if context_messages:
                    result = await agent_bridge.run_turn(
                        user_text,
                        context_messages=context_messages,
                    )
                else:
                    result = await agent_bridge.run_turn(user_text)
                final_text = result.strip() or "".join(self._assistant_parts).strip()
                if final_text:
                    await self._emit_event(
                        voice_text_event(
                            session_id=self.session_id,
                            turn_id=turn_id,
                            event="voice.assistant.final",
                            text=final_text,
                        )
                    )
                    await self._speak(final_text, turn_id=turn_id)
            except Exception:
                finish_reason = "error"
                logger.exception("Desktop voice turn failed")
                await self._emit_event(
                    voice_error_event(
                        session_id=self.session_id,
                        turn_id=turn_id,
                        message="语音任务执行失败，请换个说法或回到主会话查看。",
                        recoverable=True,
                    )
                )
            finally:
                self._active_turn_id = None
                await self._emit_event(
                    voice_state_event(
                        session_id=self.session_id,
                        turn_id=turn_id,
                        state="idle",
                        text="准备好了",
                    )
                )

            return VoiceTurnResult(
                session_id=self.session_id,
                turn_id=turn_id,
                finish_reason=finish_reason,
                mode=mode,
                context_version=context_version,
                worktree_task_id=resolved_worktree_task_id,
                worktree_path=resolved_worktree_path,
            )

    async def transcribe(self, audio_base64: str) -> str:
        if self._stt_client is None:
            raise RuntimeError("STT client is not configured")

        await self._emit_event(
            voice_state_event(session_id=self.session_id, state="transcribing", text="正在识别")
        )
        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as exc:
            raise ValueError("audioBase64 must be valid base64") from exc
        text = await self._stt_client.transcribe_audio(audio_bytes)
        await self._emit_event(
            voice_text_event(session_id=self.session_id, event="voice.user.partial", text=text)
        )
        return text

    def interrupt_turn(self) -> bool:
        if self._active_turn_id is None:
            self._audio_player.stop()
            return False
        self._agent_bridge.interrupt()
        self._audio_player.stop()
        return True

    async def _on_agent_event(self, event: AgentEvent) -> None:
        if event.type != EventType.TEXT:
            return
        text = str(event.content)
        if not text:
            return
        self._assistant_parts.append(text)
        await self._emit_event(
            voice_text_event(
                session_id=self.session_id,
                turn_id=self._active_turn_id,
                event="voice.assistant.delta",
                text=text,
            )
        )

    async def _speak(self, text: str, *, turn_id: int) -> None:
        if self._tts_muted or self._tts_client is None:
            return
        spoken = await self._agent_bridge.summarize_for_speech(text)
        if not spoken:
            return
        await self._emit_event(
            voice_state_event(
                session_id=self.session_id,
                turn_id=turn_id,
                state="speaking",
                text="正在播报",
            )
        )
        try:
            audio = await self._tts_client.synthesize_text(spoken)
            await asyncio.to_thread(self._audio_player.play_wav_bytes, audio)
        except Exception:
            logger.exception("Desktop voice TTS playback failed")
            await self._emit_event(
                voice_error_event(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    message="回复已经生成，但语音播报失败。",
                    recoverable=True,
                )
            )

    async def _emit_event(self, event: VoiceBridgeEvent | BridgeEvent) -> None:
        if self._emit is None:
            return
        result = self._emit(event)
        if result is not None:
            await result

    async def _parallel_agent(self, task_id: str) -> tuple[AgentBridge, str, str]:
        if self._worktree_manager is None or self._parallel_agent_factory is None:
            raise RuntimeError("parallel voice worktree runtime is not configured")

        resolved_task_id = task_id.strip() or f"voice-{time.time_ns()}"
        if resolved_task_id in self._parallel_agents:
            return (
                self._parallel_agents[resolved_task_id],
                resolved_task_id,
                self._parallel_worktree_paths[resolved_task_id],
            )

        workspace = await self._worktree_manager.prepare(resolved_task_id)
        agent_bridge = await self._parallel_agent_factory(str(workspace.path))
        agent_bridge.on_event(self._on_agent_event)
        self._parallel_agents[resolved_task_id] = agent_bridge
        self._parallel_worktree_paths[resolved_task_id] = str(workspace.path)
        return agent_bridge, resolved_task_id, str(workspace.path)
