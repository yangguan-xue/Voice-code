"""语音模式 CLI 入口 — V0 测试专用。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from voice_code.llm.models import init_model
from voice_code.voice.agent_bridge import AgentBridge
from voice_code.voice.audio_player import AudioPlayer
from voice_code.voice.classifier import CommandClassifier
from voice_code.voice.command_assistant import CommandAssistant
from voice_code.voice.orchestrator import VoiceOrchestrator
from voice_code.voice.segment_recorder import SegmentRecorder
from voice_code.voice.stepfun_client import (
    ASR_MODEL,
    TTS_MODEL,
    StepFunASRClient,
    StepFunTTSClient,
)
from voice_code.voice.stt_client import SttClient
from voice_code.voice.timing import TurnTiming
from voice_code.voice.tts_client import VoxcTtsClient
from voice_code.voice.types import SupportsSttClient, SupportsTtsClient, VoiceState
from voice_code.voice.wakeword import WakeWordDetector

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="reasoning-voice", description="语音开发模式 V0")
    p.add_argument("--profile", default=None, help="Model profile (models.toml)")
    p.add_argument(
        "--command-profile",
        default=None,
        help=(
            "Command assistant model profile "
            "(default: step-3.7-flash if StepFun key exists, else same as --profile)"
        ),
    )
    p.add_argument("--debug", action="store_true", help="Debug logging")
    p.add_argument("--stt-url", default="http://localhost:8765", help="STT server URL (自建模式)")
    p.add_argument("--tts-url", default="http://localhost:8775", help="TTS server URL (自建模式)")
    p.add_argument(
        "--stepfun-key", default=os.environ.get("STEPFUN_API_KEY", ""),
        help="Step Fun API Key (env: STEPFUN_API_KEY). 设置后使用 Step Fun 语音服务。",
    )
    p.add_argument(
        "--stepfun-voice", default="cixingnansheng",
        help="Step Fun TTS 音色 (default: cixingnansheng)",
    )
    p.add_argument(
        "--wake-word", default="你好小奕,你好", help="Wake word (comma-separated for multiple)"
    )
    p.add_argument("--no-wake", action="store_true", help="Skip wake word, start in listening mode")
    p.add_argument("--no-tts", action="store_true", help="Skip TTS playback")
    p.add_argument(
        "--test-audio", action="store_true",
        help="Play a test sound on startup and exit",
    )
    p.add_argument(
        "--min-db", type=float, default=-45.0,
        help="VAD 最低分贝阈值 (-60~0)，低于此值视为静音 (default: -45)",
    )
    return p.parse_args(argv)


def _setup_logging(debug: bool) -> None:
    if debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        # 非调试模式：只显示 WARNING 及以上，屏蔽 httpx、VAD 等信息
        logging.basicConfig(
            level=logging.WARNING,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )


def _on_state_change(old: VoiceState, new_state: VoiceState) -> None:
    emoji_map = {
        VoiceState.SLEEPING: "😴",
        VoiceState.LISTENING: "🎤",
        VoiceState.WORKING: "⚙️",
        VoiceState.SPEAKING: "🔊",
    }
    print(f"\n>>> {emoji_map[old]} {old.value} → {emoji_map[new_state]} {new_state.value}")


def _on_turn_timing(t: TurnTiming) -> None:
    print("\n⏱ 耗时统计:")
    print(t.format_summary())


class _VerboseTtsMixin:
    """TTS 包装混入 — 在播报前打印文本到终端。"""

    async def synthesize_text(
        self,
        text: str,
        seed: int | None = None,
        cfg_value: float | None = None,
        inference_timesteps: int | None = None,
    ) -> bytes:
        print(f"\n  🔊 播报: {text[:120]}{'...' if len(text) > 120 else ''}")
        return await super().synthesize_text(
            text,
            seed=seed,
            cfg_value=cfg_value,
            inference_timesteps=inference_timesteps,
        )  # type: ignore[misc]


class _VerboseTtsClient(_VerboseTtsMixin, VoxcTtsClient):
    """远端自建 TTS 包装器 — VoxCPM2。"""
    pass


class _VerboseStepFunTtsClient(_VerboseTtsMixin, StepFunTTSClient):
    """Step Fun TTS 包装器。"""

    async def synthesize_text(self, text: str) -> bytes:  # type: ignore[override]
        print(f"\n  🔊 播报: {text[:120]}{'...' if len(text) > 120 else ''}")
        return await StepFunTTSClient.synthesize_text(self, text)


async def run(args: argparse.Namespace) -> None:
    print("reasoning-voice: 语音开发模式 V0")

    # 检查是否使用 Step Fun
    stepfun_key = args.stepfun_key
    use_stepfun = bool(stepfun_key)

    if use_stepfun:
        print(f"  Mode: Step Fun (ASR={ASR_MODEL}, TTS={TTS_MODEL}, voice={args.stepfun_voice})")
    else:
        print("  Mode: 自建服务")
        print(f"  STT: {args.stt_url}")
        print(f"  TTS: {args.tts_url}")
    print(f"  profile: {args.profile or '(default)'}")
    print(f"  wake word: {args.wake_word}")
    print(f"  start mode: {'listening' if args.no_wake else 'sleeping (wake word)'}")
    print(f"  VAD min dB: {args.min_db}")
    print("  Press Ctrl+C to exit\n")

    # Build components
    player = AudioPlayer()

    if use_stepfun:
        stt_client: SupportsSttClient = StepFunASRClient(api_key=stepfun_key)
        tts_client: SupportsTtsClient = _VerboseStepFunTtsClient(
            api_key=stepfun_key,
            voice=args.stepfun_voice,
        )
    else:
        stt_client = SttClient(base_url=f"{args.stt_url}/transcribe")
        tts_client = _VerboseTtsClient(base_url=f"{args.tts_url}/tts")

    # Check services
    stt_ok = await stt_client.health_check()
    tts_ok = await tts_client.health_check()
    print(f"  STT health: {'OK' if stt_ok else 'FAIL'}")
    print(f"  TTS health: {'OK' if tts_ok else 'FAIL'}")
    if not stt_ok:
        print("  WARNING: STT service not reachable — voice recognition will fail")
    if not tts_ok:
        print("  WARNING: TTS service not reachable — audio playback will fail")

    # --test-audio: verify playback independently
    if args.test_audio:
        print("\n  [TEST] 正在 TTS 合成并播放测试音频...")
        try:
            audio = await tts_client.synthesize_text("测试音频播放正常。我是语音编程助手。")
            print(f"  [TEST] TTS 返回 {len(audio)} bytes")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, player.play_wav_bytes, audio)
            print("  [TEST] 播放完成 ✓")
        except Exception as e:
            print(f"  [TEST] 播放失败: {e}")
            import traceback
            traceback.print_exc()
        print()
        return

    # Agent bridge — summary model 用于口语总结
    if use_stepfun:
        summary_model = ChatOpenAI(
            model="step-3.7-flash",
            base_url="https://api.stepfun.com/step_plan/v1",
            api_key=SecretStr(stepfun_key) if stepfun_key else SecretStr(""),
            temperature=0.0,
            timeout=10.0,
        )
    else:
        summary_model = init_model(
            profile=args.profile,
            temperature=0.0,
            timeout=10.0,
        )
    bridge = AgentBridge(profile=args.profile, summary_model=summary_model)
    await bridge.start()
    print(f"  Agent ready: session={bridge._runtime.session_id}")  # type: ignore[union-attr]

    # Command assistant model
    if use_stepfun and not args.command_profile:
        command_model = ChatOpenAI(
            model="step-3.7-flash",
            base_url="https://api.stepfun.com/step_plan/v1",
            api_key=SecretStr(stepfun_key) if stepfun_key else SecretStr(""),
            temperature=0.0,
            timeout=10.0,
        )
    else:
        command_model = init_model(
            profile=args.command_profile or args.profile,
            temperature=0.0,
            timeout=10.0,
        )
    command_assistant = CommandAssistant(model=command_model)

    # Classifier (轻量 LLM 门禁，max_tokens=5)
    classifier = None
    if bridge._runtime is not None:  # type: ignore[union-attr]
        classifier = CommandClassifier(model=bridge._runtime.model)  # type: ignore[union-attr]

    # Wake word
    wake_words = [w.strip() for w in args.wake_word.split(",") if w.strip()]
    wakeword = WakeWordDetector(wake_words=wake_words if wake_words else None)

    # Orchestrator
    orchestrator = VoiceOrchestrator(
        stt_client=stt_client,
        tts_client=tts_client,
        agent_bridge=bridge,
        classifier=classifier,
        wakeword=wakeword,
        segment_recorder=SegmentRecorder(min_db=args.min_db),
        audio_player=player,
        command_assistant=command_assistant,
        profile=args.profile,
    )
    orchestrator.on_state_change(_on_state_change)
    orchestrator.on_turn_timing(_on_turn_timing)

    # Start in listening mode if --no-wake
    if args.no_wake:
        # Patch: start orchestrator, then directly enter listening
        await orchestrator.start()
        # Force transition to listening (bypass wake word)
        await orchestrator._enter_listening()  # type: ignore[attr-used]
        print("\n>>> 已跳过唤醒词，直接进入聆听模式。请说话...")
    else:
        await orchestrator.start()
        print("\n>>> 正在监听唤醒词... 说\"codex\"唤醒我。")

    # Keep running until Ctrl+C
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _on_sigint() -> None:
        print("\n正在退出...")
        stop_event.set()

    loop.add_signal_handler(signal.SIGINT, _on_sigint)
    try:
        await stop_event.wait()
    finally:
        await orchestrator.stop()
        print("已退出语音模式。")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    _setup_logging(args.debug)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
