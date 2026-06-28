"""语音编排器 — V0 状态机，协调唤醒词 / VAD / STT / 分类 / Agent / TTS。"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import re
import struct
import threading
import time
import wave
from collections.abc import Callable

from rich.console import Console
from rich.markdown import Markdown as RichMarkdown

from voice_code.agent.types import AgentEvent, EventType
from voice_code.voice.agent_bridge import AgentBridge
from voice_code.voice.audio_player import AudioPlayer
from voice_code.voice.classifier import CommandClassifier, CommandKind
from voice_code.voice.command_assistant import CommandAssistant
from voice_code.voice.segment_recorder import SegmentRecorder
from voice_code.voice.timing import TimingCollector, TurnTiming
from voice_code.voice.types import (
    AGENT_FAILED_TEXT,
    CHANNELS,
    IDLE_TIMEOUT_SECONDS,
    SAMPLE_RATE,
    SLEEP_CONFIRM_TEXT,
    STATUS_TEXT_MAP,
    STT_NOT_CLEAR_TEXT,
    SUPERVISOR_FAILED_TEXT,
    SUPERVISOR_MAX_DISPATCHES,
    SUPERVISOR_MAX_REVIEWS,
    SUPERVISOR_MAX_TURN_SECONDS,
    TTS_MAX_TEXT_CHARS,
    WAKE_CONFIRM_TEXT,
    SupervisorAction,
    SupervisorDecision,
    SupportsSttClient,
    SupportsTtsClient,
    VoiceState,
)
from voice_code.voice.voice_display import VoiceDisplay
from voice_code.voice.wakeword import WakeWordDetector

logger = logging.getLogger(__name__)

_rich_console = Console()

# 唤醒词音频累积：~2 秒 @ 16kHz mono 16bit
_WAKE_CHUNK_SECONDS = 2.0
_WAKE_CHUNK_BYTES = int(SAMPLE_RATE * _WAKE_CHUNK_SECONDS * 2)
# 唤醒检测 RMS 阈值：低于此 dB 的音频块不送 STT（静音过滤）
_WAKE_RMS_THRESHOLD_DB = -35.0


class VoiceOrchestrator:
    """语音开发模式 V0 编排器。

    管理 4 态状态机:
      sleeping -> listening -> working -> speaking -> listening

    麦克风在 start() 时打开、stop() 时关闭，全程运行。
    sleeping 态：音频帧累积后送唤醒词检测。
    listening 态：音频帧经 VAD 分段后送 STT → 分类 → Agent/TTS。
    """

    def __init__(
        self,
        stt_client: SupportsSttClient,
        tts_client: SupportsTtsClient,
        agent_bridge: AgentBridge,
        classifier: CommandClassifier | None = None,
        wakeword: WakeWordDetector | None = None,
        segment_recorder: SegmentRecorder | None = None,
        audio_player: AudioPlayer | None = None,
        command_assistant: CommandAssistant | None = None,
        profile: str | None = None,
    ) -> None:
        self._stt = stt_client
        self._tts = tts_client
        self._bridge = agent_bridge
        self._classifier = classifier
        self._wakeword = wakeword
        self._recorder = segment_recorder or SegmentRecorder()
        self._player = audio_player or AudioPlayer()
        self._command_assistant = command_assistant
        self._profile = profile

        self._bridge.on_event(self._on_agent_event)

        self._state: VoiceState = VoiceState.SLEEPING
        self._state_lock = threading.Lock()
        self._idle_task: asyncio.Task[None] | None = None
        self._last_event_time = 0.0
        self._running = False
        self._on_state_change_callbacks: list[Callable[[VoiceState, VoiceState], None]] = []

        # 计时器
        self._timing = TimingCollector()
        self._on_turn_timing_callbacks: list[Callable[[TurnTiming], None]] = []

        # 缓存主线程 event loop — 音频回调线程用它投递 async 任务
        self._loop: asyncio.AbstractEventLoop | None = None

        # 唤醒词音频累积缓冲区（sleeping 态使用）
        self._wake_buf: bytearray = bytearray()
        self._wake_lock = threading.Lock()
        self._wake_check_scheduled = False
        self._wake_triggered = False  # 防重复触发

        # agent 事件流文本缓冲（防逐字打印）
        self._agent_text_buf = ""

        self._display = VoiceDisplay()
        self._display_frame = 0

        self._recorder.on_segment(self._on_speech_segment)
        self._recorder.on_raw_frame(self._on_raw_frame)

        # NOTE: 不注册 on_wake 回调，唤醒由 _check_wake_word 统一处理。
        # 否则 detect_from_bytes 的回调 + 返回 True 会双重触发。

        logger.info("VoiceOrchestrator initialized: state=%s", self._state)

    @property
    def state(self) -> VoiceState:
        with self._state_lock:
            return self._state

    def on_state_change(self, callback: Callable[[VoiceState, VoiceState], None]) -> None:
        """注册状态变更回调 callback(old_state, new_state)。"""
        self._on_state_change_callbacks.append(callback)

    def on_turn_timing(self, callback: Callable[[TurnTiming], None]) -> None:
        """注册每轮耗时回调。"""
        self._on_turn_timing_callbacks.append(callback)

    # ---- Lifecycle ----

    async def start(self) -> None:
        """启动语音编排器：初始化 AgentBridge + 缓存 event loop + 打开麦克风。"""
        logger.info("VoiceOrchestrator: starting")
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._display.start()
        await self._bridge.start()
        self._recorder.open_mic()
        self._reset_idle_timer()
        logger.info("VoiceOrchestrator: started in state=%s, mic open", self._state)

    async def stop(self) -> None:
        """停止语音编排器：关闭麦克风 + 清理所有任务。"""
        logger.info("VoiceOrchestrator: stopping")
        self._running = False
        if self._idle_task:
            self._idle_task.cancel()
            self._idle_task = None
        self._recorder.stop()
        self._recorder.close_mic()
        self._player.stop()
        self._display.stop()
        await self._bridge.shutdown()
        logger.info("VoiceOrchestrator: stopped")

    # ---- Public handlers (called from audio thread via loop.call_soon_threadsafe) ----

    async def handle_wake_word(self) -> None:
        """处理唤醒词检测。"""
        self._wake_triggered = True
        _rich_console.print("  ✅ 唤醒成功")
        await self._transition_to(VoiceState.LISTENING, "wake_word")
        await self._speak(WAKE_CONFIRM_TEXT)

    async def handle_speech_segment(self, audio_bytes: bytes) -> None:
        """处理一段完整语音段。"""
        if self.state != VoiceState.LISTENING:
            logger.debug("Orchestrator: ignoring speech in state %s", self.state)
            return

        if not audio_bytes or len(audio_bytes) < 44:
            logger.debug("Orchestrator: empty audio segment")
            return

        # Step 1: STT
        self._timing.start_stage()
        try:
            text = await self._stt.transcribe_audio(audio_bytes)
        except RuntimeError:
            logger.warning("Orchestrator: STT failed")
            self._timing.end_stt()
            await self._speak(STT_NOT_CLEAR_TEXT)
            self._reset_idle_timer()
            return
        self._timing.end_stt()

        if not text.strip():
            self._reset_idle_timer()
            return

        stripped = text.strip()
        # 无中文的短文本（Yeah、the、.等） → 非命令
        if not re.search(r'[\u4e00-\u9fff]', stripped) and len(stripped) < 10:
            logger.info("Orchestrator: ignoring non-Chinese filler '%s'", stripped)
            self._reset_idle_timer()
            return

        # 嗯开头的犹豫词 → 非命令
        if re.match(r'^嗯+', stripped) and len(stripped) < 15:
            logger.info("Orchestrator: ignoring hesitation '%s'", stripped)
            self._reset_idle_timer()
            return

        _rich_console.print(f"  🎤 {text[:200]}")

        # Step 2: Classify
        self._timing.start_stage()
        if self._classifier is not None:
            decision = await self._classifier.classify(text)
        else:
            from voice_code.voice.types import CommandDecision
            decision = CommandDecision(kind=CommandKind.AGENT_COMMAND, text=text)
        self._timing.end_classify()

        logger.info("Orchestrator: classified -> %s", decision.kind.value)

        # Step 3: Route
        if decision.kind == CommandKind.AGENT_COMMAND:
            self._reset_idle_timer()
            await self._handle_user_command(decision.text)

        elif decision.kind == CommandKind.CONTROL_COMMAND:
            self._reset_idle_timer()
            await self._handle_control_command(decision.control_name)

        else:
            logger.debug("Orchestrator: ignoring speech")
            self._reset_idle_timer()

    # ---- State Machine ----

    async def _transition_to(self, new_state: VoiceState, reason: str = "") -> None:
        old = self.state
        if old == new_state:
            return
        with self._state_lock:
            self._state = new_state
        logger.info("Orchestrator: %s -> %s (%s)", old.value, new_state.value, reason)
        for cb in self._on_state_change_callbacks:
            try:
                cb(old, new_state)
            except Exception:
                logger.exception("Orchestrator: state change callback error")

    async def _enter_sleeping(self) -> None:
        await self._transition_to(VoiceState.SLEEPING, "idle_timeout_or_command")
        self._recorder.close_mic()
        self._wake_triggered = False
        with self._wake_lock:
            self._wake_buf = bytearray()

    async def _enter_listening(self) -> None:
        # 开麦
        self._recorder.open_mic()
        self._recorder.drain_queue()
        await self._transition_to(VoiceState.LISTENING, "wake_or_resume")
        self._recorder.start()

    async def _enter_working(self) -> None:
        await self._transition_to(VoiceState.WORKING, "agent_submitted")

    async def _enter_speaking(self) -> None:
        # 播报前彻底关麦（关闭硬件 InputStream），避免与播放器 OutputStream 冲突
        self._recorder.close_mic()
        await self._transition_to(VoiceState.SPEAKING, "tts_started")

    # ---- Control Commands ----

    async def _handle_control_command(self, control_name: str) -> None:
        logger.info("Orchestrator: control command: %s", control_name)

        if control_name == "stop_agent":
            if self.state == VoiceState.WORKING:
                self._bridge.interrupt()
                logger.info("Orchestrator: agent interrupted")
            await self._enter_listening()

        elif control_name == "stop_speaking":
            if self.state == VoiceState.SPEAKING:
                self._player.stop()
                logger.info("Orchestrator: playback stopped")
            await self._enter_listening()

        elif control_name == "report_status":
            status_text = STATUS_TEXT_MAP.get(self.state, "未知状态")
            await self._speak(status_text)

        elif control_name == "sleep":
            await self._speak(SLEEP_CONFIRM_TEXT)
            await self._enter_sleeping()

        elif control_name == "keep_alive":
            self._reset_idle_timer()
            logger.info("Orchestrator: idle timer reset")

    # ---- Agent Submission ----

    async def _handle_user_command(self, text: str) -> None:
        if self._command_assistant is None:
            await self._submit_agent_command(text)
            return

        decision = await self._command_assistant.decide_user_turn(text)
        self._command_assistant.apply_context_update(decision.context_update)
        await self._handle_supervisor_decision(
            decision,
            user_text=text,
            started_at=time.monotonic(),
            review_count=0,
        )

    async def _handle_supervisor_decision(
        self,
        decision: SupervisorDecision,
        *,
        user_text: str,
        started_at: float,
        review_count: int,
    ) -> None:
        if decision.action == SupervisorAction.DISPATCH:
            await self._run_supervised_dispatch_loop(
                user_text=user_text,
                initial_task=decision.task,
                started_at=started_at,
                review_count=review_count,
            )
            return

        if decision.action in {
            SupervisorAction.REPLY,
            SupervisorAction.ASK_USER,
            SupervisorAction.FINISH,
            SupervisorAction.FAIL,
        }:
            await self._speak(decision.message or SUPERVISOR_FAILED_TEXT)
            self._reset_idle_timer()
            self._finish_turn()
            return

        logger.warning("Orchestrator: unsupported supervisor action: %s", decision.action)
        await self._speak(SUPERVISOR_FAILED_TEXT)
        self._reset_idle_timer()
        self._finish_turn()

    async def _run_supervised_dispatch_loop(
        self,
        *,
        user_text: str,
        initial_task: str,
        started_at: float,
        review_count: int,
    ) -> None:
        if self._command_assistant is None:
            await self._submit_agent_command(initial_task)
            return

        task = initial_task
        dispatch_count = 0
        current_review_count = review_count

        while True:
            if dispatch_count >= SUPERVISOR_MAX_DISPATCHES:
                await self._speak("哥哥，连续执行次数太多了，先停一下，麻烦你重新说。")
                self._reset_idle_timer()
                self._finish_turn()
                return

            if current_review_count >= SUPERVISOR_MAX_REVIEWS:
                await self._speak("哥哥，反复修改次数太多了，先停一下，麻烦你重新说。")
                self._reset_idle_timer()
                self._finish_turn()
                return

            if time.monotonic() - started_at >= SUPERVISOR_MAX_TURN_SECONDS:
                await self._speak("哥哥，用时太长了，先停一下，麻烦你重新说。")
                self._reset_idle_timer()
                self._finish_turn()
                return

            dispatch_count += 1
            result = await self._run_agent_turn(task)
            if result is None:
                await self._speak(AGENT_FAILED_TEXT)
                await self._enter_listening()
                self._reset_idle_timer()
                self._finish_turn()
                return

            current_review_count += 1
            decision = await self._command_assistant.review_agent_result(
                user_text=user_text,
                task=task,
                agent_result=result,
                dispatch_count=dispatch_count,
            )
            self._command_assistant.apply_context_update(decision.context_update)

            if decision.action == SupervisorAction.CONTINUE:
                task = decision.task
                continue

            if decision.action in {
                SupervisorAction.FINISH,
                SupervisorAction.ASK_USER,
                SupervisorAction.FAIL,
            }:
                await self._speak(decision.message or result or SUPERVISOR_FAILED_TEXT)
                await self._enter_listening()
                self._reset_idle_timer()
                self._finish_turn()
                return

            logger.warning("Orchestrator: unexpected review action %s", decision.action)
            await self._speak(SUPERVISOR_FAILED_TEXT)
            await self._enter_listening()
            self._reset_idle_timer()
            self._finish_turn()
            return

    async def _submit_agent_command(self, text: str) -> None:
        _rich_console.print("  ⚙️ 处理中…")
        result = await self._run_agent_turn(text)
        if result is None:
            await self._speak(AGENT_FAILED_TEXT)
            await self._enter_listening()
            self._reset_idle_timer()
            self._finish_turn()
            return

        if result.strip():
            spoken = await self._bridge.summarize_for_speech(result)
            await self._enter_speaking()
            await self._speak(spoken)
            await self._enter_listening()
        else:
            logger.info("Orchestrator: agent returned empty result")
            await self._enter_listening()

        self._reset_idle_timer()
        self._finish_turn()

    async def _run_agent_turn(self, text: str) -> str | None:
        await self._enter_working()
        self._timing.start_stage()
        result_len = 0
        try:
            result = await self._bridge.run_turn(text)
            result_len = len(result)
        except RuntimeError as e:
            logger.error("Orchestrator: agent failed: %s", e)
            self._timing.end_agent(result_len)
            return None
        self._timing.end_agent(result_len)
        return result

    # ---- TTS + Playback ----

    async def _speak(self, text: str) -> None:
        """将文本合成语音并播放（流式）。

        调用者应负责预先调用 bridge.summarize_for_speech（如果需要对长文本摘要）。
        _speak 自身只做长度兜底截断。
        """
        if not text or not text.strip():
            return

        tts_text = text.strip()
        # 清除符号、表情，避免 TTS 误读
        tts_text = re.sub(
            r'[\U0001F300-\U0001F9FF`*_~#\-/\\()（）【】\[\]{}]',
            '', tts_text,
        ).strip()

        if len(tts_text) > TTS_MAX_TEXT_CHARS:
            tts_text = tts_text[:TTS_MAX_TEXT_CHARS] + "。以上为部分内容，完整回复请看终端。"

        logger.info("Orchestrator: speaking '%s'", tts_text)
        self._timing.start_stage()

        try:
            stream = self._tts.synthesize_stream(
                tts_text,
                seed=1,
                cfg_value=2.0,
                inference_timesteps=10,
            )
        except Exception:
            logger.exception("Orchestrator: TTS failed, text preserved in logs")
            self._timing.end_tts(0)
            try:
                audio = await self._tts.synthesize_text(tts_text, seed=1)
                self._timing.end_tts(len(audio))
                prev_state = self.state
                if prev_state != VoiceState.SPEAKING:
                    await self._enter_speaking()
                self._timing.start_stage()
                await self._loop.run_in_executor(None, self._player.play_wav_bytes, audio)
                self._timing.end_playback()
                if prev_state != VoiceState.SPEAKING:
                    await self._enter_listening()
            except Exception:
                logger.exception("Orchestrator: fallback TTS also failed")
            return

        try:
            prev_state = self.state
            if prev_state != VoiceState.SPEAKING:
                await self._enter_speaking()

            first = True
            async for chunk, sr in stream:
                if first:
                    self._timing.end_tts(0)
                    self._timing.start_stage()
                    first = False
                await self._player.play_chunk(chunk, sr)
            await self._player.close_stream()
            self._timing.end_playback()

            if prev_state != VoiceState.SPEAKING:
                await self._enter_listening()
        except Exception:
            logger.exception("Orchestrator: stream playback failed")
            self._timing.end_playback()
            if self.state == VoiceState.SPEAKING:
                await self._enter_listening()

    # ---- Idle Timeout ----

    def _reset_idle_timer(self) -> None:
        """重置空闲计时器（从 event loop 线程调用）。"""
        if self._loop is None:
            return
        self._last_event_time = self._loop.time()
        if self._idle_task and not self._idle_task.done():
            return
        self._idle_task = asyncio.create_task(self._idle_watcher())

    async def _idle_watcher(self) -> None:
        """监控空闲超时。"""
        while self._running:
            elapsed = asyncio.get_event_loop().time() - self._last_event_time
            if elapsed >= IDLE_TIMEOUT_SECONDS:
                logger.info("Orchestrator: idle timeout (%.0fs)", elapsed)
                if self.state != VoiceState.SLEEPING:
                    await self._speak(SLEEP_CONFIRM_TEXT)
                    await self._enter_sleeping()
                break
            await asyncio.sleep(1)

    # ---- Raw audio frame routing (called from mic thread) ----

    def _on_raw_frame(self, frame_bytes: bytes) -> None:
        """每帧音频回调 — 计算 RMS + 更新显示 + 唤醒词检测。"""
        if not self._running:
            return

        # 计算 RMS 分贝（所有状态）
        rms_db = self._frame_rms_db(frame_bytes)

        # 更新显示（每 3 帧 ~10fps）
        self._display_frame += 1
        if self._display_frame % 3 == 0:
            self._display.update(self.state, rms_db)

        # sleeping 态：唤醒词检测
        if self.state != VoiceState.SLEEPING:
            return

        with self._wake_lock:
            self._wake_buf.extend(frame_bytes)
            if len(self._wake_buf) < _WAKE_CHUNK_BYTES:
                return
            chunk = bytes(self._wake_buf[:_WAKE_CHUNK_BYTES])
            self._wake_buf = self._wake_buf[_WAKE_CHUNK_BYTES:]

        # 构建简短 WAV
        wav_bytes = self._build_short_wav(chunk)
        # 投递到 event loop 做异步唤醒词检测
        self._loop.call_soon_threadsafe(  # type: ignore[union-attr]
            lambda: asyncio.create_task(self._check_wake_word(wav_bytes))
        )

    async def _check_wake_word(self, wav_bytes: bytes) -> None:
        """异步检查音频片段是否含唤醒词。"""
        if self.state != VoiceState.SLEEPING or self._wakeword is None or self._wake_triggered:
            return
        # 能量门禁：太安静就不送 STT（节省 API 调用，避免噪音误唤醒）
        if not self._wake_chunk_energy_ok(wav_bytes):
            return
        try:
            text = await self._stt.transcribe_audio(wav_bytes)
        except RuntimeError:
            return
        detected = await self._wakeword.detect_from_bytes(wav_bytes, text)
        if detected:
            await self.handle_wake_word()

    @staticmethod
    def _frame_rms_db(frame_bytes: bytes) -> float:
        """计算一帧 16-bit PCM 的 RMS 分贝值 (dB FS)。"""
        n = len(frame_bytes) // 2
        if n == 0:
            return -100.0
        samples = struct.unpack(f"<{n}h", frame_bytes)
        sum_sq = sum(float(s) * float(s) for s in samples)
        rms = math.sqrt(sum_sq / n)
        if rms < 1.0:
            return -100.0
        return 20.0 * math.log10(rms / 32768.0)

    @staticmethod
    def _wake_chunk_energy_ok(wav_bytes: bytes) -> bool:
        """检查 WAV 音频块的 RMS 分贝是否超过唤醒阈值。"""
        # 跳过 WAV 头 (44 bytes) 取 PCM
        pcm = wav_bytes[44:] if len(wav_bytes) > 44 else wav_bytes
        n = len(pcm) // 2
        if n == 0:
            return False
        samples = struct.unpack(f"<{n}h", pcm)
        sum_sq = sum(float(s) * float(s) for s in samples)
        rms = math.sqrt(sum_sq / n)
        if rms < 1.0:
            return False
        db = 20.0 * math.log10(rms / 32768.0)
        return db >= _WAKE_RMS_THRESHOLD_DB

    async def _on_agent_event(self, event: AgentEvent) -> None:
        """agent 事件流式回调 — 缓冲完整段落再渲染 Markdown。"""
        t = event.type

        if t == EventType.TEXT:
            chunk = str(event.content)
            if not chunk:
                return
            self._agent_text_buf += chunk
            while "\n\n" in self._agent_text_buf:
                block, self._agent_text_buf = self._agent_text_buf.split("\n\n", 1)
                self._render_md(block)

        elif t == EventType.FINISH:
            if self._agent_text_buf.strip():
                self._render_md(self._agent_text_buf)
                self._agent_text_buf = ""
            _rich_console.print("  ✅ 完成")

    def _render_md(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if text.replace("|", "").replace("-", "").strip():
            _rich_console.print(RichMarkdown(text))

    # ---- Internal callbacks (from mic thread) ----

    def _on_speech_segment(self, audio_bytes: bytes, speech_start_time: float) -> None:
        """VAD 分段完成回调 — 从 mic 线程投递到 event loop。"""
        if not self._running or self.state != VoiceState.LISTENING:
            return
        seg_arrival = time.monotonic()
        self._loop.call_soon_threadsafe(  # type: ignore[union-attr]
            lambda: asyncio.create_task(
                self._handle_segment_timed(audio_bytes, speech_start_time, seg_arrival)
            )
        )

    async def _handle_segment_timed(
        self, audio_bytes: bytes, speech_start_time: float, seg_arrival: float
    ) -> None:
        """带计时的语音段处理。"""
        # VAD 耗时 = 段到达时间 - 首次语音时间
        if speech_start_time > 0:
            self._timing._current.vad_wall_seconds = seg_arrival - speech_start_time
        await self.handle_speech_segment(audio_bytes)

    def _finish_turn(self) -> None:
        """结束当前轮计时，通知回调。"""
        turn = self._timing.finish_turn()
        for cb in self._on_turn_timing_callbacks:
            try:
                cb(turn)
            except Exception:
                logger.exception("Orchestrator: turn timing callback error")

    @staticmethod
    def _build_short_wav(pcm_bytes: bytes) -> bytes:
        """将原始 16-bit PCM 打包为 WAV。"""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_bytes)
        return buf.getvalue()
