from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

from voice_code.voice.orchestrator import _WAKE_CHUNK_BYTES, VoiceOrchestrator
from voice_code.voice.types import CommandDecision, CommandKind, VoiceState


class _Recorder:
    def __init__(self) -> None:
        self.open_count = 0
        self.close_count = 0
        self.start_count = 0
        self.stop_count = 0

    def on_segment(self, callback) -> None:
        self.segment_callback = callback

    def on_raw_frame(self, callback) -> None:
        self.frame_callback = callback

    def open_mic(self) -> None:
        self.open_count += 1

    def close_mic(self) -> None:
        self.close_count += 1

    def drain_queue(self) -> None:
        return None

    def start(self) -> None:
        self.start_count += 1

    def stop(self) -> None:
        self.stop_count += 1


class _Bridge:
    def __init__(self, *, result: str = "完成", error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []
        self.interrupt_count = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    def on_event(self, callback) -> None:
        self.callback = callback

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def run_turn(self, text: str) -> str:
        self.calls.append(text)
        self.started.set()
        if self.error is not None:
            raise self.error
        if self.block:
            await self.release.wait()
        return self.result

    async def summarize_for_speech(self, text: str) -> str:
        return text

    def interrupt(self) -> None:
        self.interrupt_count += 1


class _Classifier:
    def __init__(self, decision: CommandDecision) -> None:
        self.decision = decision

    async def classify(self, _text: str) -> CommandDecision:
        return self.decision


class _Player:
    def __init__(self, *, fallback_error: Exception | None = None) -> None:
        self.stop_count = 0
        self.begin_count = 0
        self.close_count = 0
        self.wav_calls = 0
        self.fallback_error = fallback_error

    def begin_stream(self) -> None:
        self.begin_count += 1

    def stop(self) -> None:
        self.stop_count += 1

    def stop_requested(self) -> bool:
        return False

    async def play_chunk(self, _chunk: np.ndarray, _sample_rate: int) -> bool:
        return True

    async def close_stream(self) -> None:
        self.close_count += 1

    def play_wav_bytes(self, _audio: bytes) -> None:
        self.wav_calls += 1
        if self.fallback_error is not None:
            raise self.fallback_error


class _FailingStreamTts:
    def __init__(self) -> None:
        self.fallback_calls = 0

    def synthesize_stream(self, _text: str, **_: object):
        async def stream():
            yield np.zeros(8, dtype=np.float32), 16_000
            raise ValueError("stream provider failed")

        return stream()

    async def synthesize_text(self, _text: str, **_: object) -> bytes:
        self.fallback_calls += 1
        return b"RIFF" + b"\x00" * 64


class _WakeWord:
    async def detect_from_bytes(self, _audio: bytes, _text: str) -> bool:
        return False


def _make_orchestrator(
    *,
    bridge: _Bridge | None = None,
    decision: CommandDecision | None = None,
    recorder: _Recorder | None = None,
    player: _Player | None = None,
    tts: Any | None = None,
    tts_enabled: bool = False,
    stt_text: str = "执行任务",
) -> tuple[VoiceOrchestrator, _Bridge, _Recorder, _Player]:
    actual_bridge = bridge or _Bridge()
    actual_recorder = recorder or _Recorder()
    actual_player = player or _Player()

    async def transcribe(_audio: bytes) -> str:
        return stt_text

    orchestrator = VoiceOrchestrator(
        stt_client=cast(Any, SimpleNamespace(transcribe_audio=transcribe)),
        tts_client=cast(Any, tts or _FailingStreamTts()),
        agent_bridge=cast(Any, actual_bridge),
        classifier=cast(
            Any,
            _Classifier(
                decision
                or CommandDecision(kind=CommandKind.AGENT_COMMAND, text=stt_text)
            ),
        ),
        segment_recorder=cast(Any, actual_recorder),
        audio_player=cast(Any, actual_player),
        display=cast(Any, SimpleNamespace(
            start=lambda: None,
            stop=lambda: None,
            set_state=lambda _state: None,
            update=lambda _state, _db: None,
            set_transcript=lambda _text: None,
            clear_agent_output=lambda: None,
            append_agent_text=lambda _text: None,
            set_notice=lambda _text: None,
            set_timing=lambda _timing: None,
            set_delegations=lambda _items: None,
        )),
        console_output=False,
        tts_enabled=tts_enabled,
    )
    orchestrator._loop = asyncio.get_running_loop()
    orchestrator._running = True
    return orchestrator, actual_bridge, actual_recorder, actual_player


@pytest.mark.asyncio
async def test_sleeping_keeps_microphone_open_for_wake_detection() -> None:
    orchestrator, _, recorder, _ = _make_orchestrator()

    await orchestrator._enter_sleeping()

    assert orchestrator.state is VoiceState.SLEEPING
    assert recorder.open_count == 1
    assert recorder.close_count == 0
    assert recorder.stop_count == 1


@pytest.mark.asyncio
async def test_control_command_can_interrupt_working_turn() -> None:
    bridge = _Bridge()
    recorder = _Recorder()
    orchestrator, _, _, player = _make_orchestrator(
        bridge=bridge,
        recorder=recorder,
        decision=CommandDecision(
            kind=CommandKind.CONTROL_COMMAND,
            text="停止",
            control_name="stop_agent",
        ),
        stt_text="停止",
    )
    await orchestrator._enter_working()

    await orchestrator.handle_speech_segment(b"RIFF" + b"\x00" * 64)

    assert bridge.interrupt_count == 1
    assert player.stop_count == 0
    assert orchestrator.state is VoiceState.LISTENING


@pytest.mark.asyncio
async def test_control_command_can_interrupt_speaking_turn() -> None:
    orchestrator, bridge, _, player = _make_orchestrator(
        decision=CommandDecision(
            kind=CommandKind.CONTROL_COMMAND,
            text="停止",
            control_name="stop_agent",
        ),
        stt_text="停止",
    )
    await orchestrator._enter_speaking()

    await orchestrator.handle_speech_segment(b"RIFF" + b"\x00" * 64)

    assert bridge.interrupt_count == 0
    assert player.stop_count == 1
    assert orchestrator.state is VoiceState.LISTENING


@pytest.mark.asyncio
async def test_second_agent_segment_is_dropped_while_first_turn_runs() -> None:
    bridge = _Bridge()
    bridge.block = True
    orchestrator, _, _, _ = _make_orchestrator(bridge=bridge)
    await orchestrator._enter_listening()
    first = asyncio.create_task(
        orchestrator.handle_speech_segment(b"RIFF" + b"\x00" * 64)
    )
    await asyncio.wait_for(bridge.started.wait(), timeout=1)

    await orchestrator.handle_speech_segment(b"RIFF" + b"\x00" * 64)
    bridge.release.set()
    await first

    assert bridge.calls == ["执行任务"]


@pytest.mark.asyncio
async def test_pause_survives_background_agent_completion() -> None:
    bridge = _Bridge()
    bridge.block = True
    orchestrator, _, _, _ = _make_orchestrator(bridge=bridge)
    await orchestrator._enter_listening()
    first = asyncio.create_task(
        orchestrator.handle_speech_segment(b"RIFF" + b"\x00" * 64)
    )
    await asyncio.wait_for(bridge.started.wait(), timeout=1)

    await orchestrator.pause_or_resume()
    assert orchestrator.state is VoiceState.PAUSED

    bridge.release.set()
    await first

    assert orchestrator.state is VoiceState.PAUSED


@pytest.mark.asyncio
async def test_non_runtime_agent_exception_returns_to_listening() -> None:
    bridge = _Bridge(error=ValueError("provider failure"))
    orchestrator, _, _, _ = _make_orchestrator(bridge=bridge)
    await orchestrator._enter_listening()

    await orchestrator.handle_speech_segment(b"RIFF" + b"\x00" * 64)

    assert orchestrator.state is VoiceState.LISTENING
    assert bridge.calls == ["执行任务"]


@pytest.mark.asyncio
async def test_stream_iteration_failure_uses_complete_audio_fallback() -> None:
    tts = _FailingStreamTts()
    player = _Player()
    orchestrator, _, _, _ = _make_orchestrator(
        player=player,
        tts=tts,
        tts_enabled=True,
    )
    await orchestrator._enter_listening()

    await orchestrator._speak("请播报结果")

    assert tts.fallback_calls == 1
    assert player.wav_calls == 1
    assert orchestrator.state is VoiceState.LISTENING


@pytest.mark.asyncio
async def test_fallback_playback_failure_does_not_leave_speaking_state() -> None:
    tts = _FailingStreamTts()
    player = _Player(fallback_error=RuntimeError("speaker unavailable"))
    orchestrator, _, _, _ = _make_orchestrator(
        player=player,
        tts=tts,
        tts_enabled=True,
    )
    await orchestrator._enter_listening()

    await orchestrator._speak("请播报结果")

    assert orchestrator.state is VoiceState.LISTENING


@pytest.mark.asyncio
async def test_wake_detection_backpressure_allows_only_one_stt_check() -> None:
    orchestrator, _, _, _ = _make_orchestrator()
    orchestrator._wakeword = _WakeWord()
    scheduled: list[Any] = []

    def capture(awaitable, **_: object):
        scheduled.append(awaitable)
        return None

    orchestrator._start_background_task = capture  # type: ignore[method-assign]
    frame = (b"\x00\x10" * (_WAKE_CHUNK_BYTES // 2))

    orchestrator._on_raw_frame(frame)
    orchestrator._on_raw_frame(frame)
    await asyncio.sleep(0)

    assert len(scheduled) == 1
    await scheduled[0]
    assert orchestrator._wake_check_scheduled is False
