from __future__ import annotations

import asyncio
import signal

import pytest

from voice_code import voice_cli
from voice_code.voice.segment_recorder import SegmentRecorder
from voice_code.voice_cli import _install_stop_handler, parse_args


def test_plain_display_stop_handler_falls_back_when_signal_handlers_are_unsupported(
    monkeypatch,
) -> None:
    stop_event = asyncio.Event()
    registered: list[object] = []

    class Loop:
        def add_signal_handler(self, *_args) -> None:
            raise NotImplementedError

    monkeypatch.setattr(signal, "signal", lambda *_args: registered.append(_args[1]))

    _install_stop_handler(Loop(), stop_event)

    assert registered
    registered[0](signal.SIGINT, None)
    assert stop_event.is_set()


def test_no_tts_flag_is_preserved_by_argument_parser() -> None:
    assert parse_args(["--no-tts"]).no_tts is True


@pytest.mark.asyncio
async def test_no_tts_skips_synthesis_in_test_audio_path(monkeypatch) -> None:
    class FakeStt:
        def __init__(self, **_kwargs) -> None:
            pass

        async def health_check(self) -> bool:
            return True

    class FakeTts:
        synthesize_calls = 0

        def __init__(self, **_kwargs) -> None:
            pass

        async def health_check(self) -> bool:
            return True

        async def synthesize_text(self, _text: str, **_kwargs) -> bytes:
            self.synthesize_calls += 1
            return b"RIFF"

    monkeypatch.setattr(voice_cli, "SttClient", FakeStt)
    monkeypatch.setattr(voice_cli, "VoxcTtsClient", FakeTts)

    await voice_cli.run(parse_args(["--no-tts", "--test-audio"]))

    assert FakeTts.synthesize_calls == 0


def test_microphone_open_failure_has_actionable_windows_message(monkeypatch) -> None:
    class FailingInputStream:
        def __init__(self, **_kwargs) -> None:
            raise OSError("no input device")

    class FakeSounddevice:
        InputStream = FailingInputStream

    monkeypatch.setattr(
        "voice_code.voice.segment_recorder._sounddevice",
        lambda: FakeSounddevice,
    )
    recorder = SegmentRecorder()

    with pytest.raises(RuntimeError, match="麦克风"):
        recorder.open_mic()

    assert recorder._mic_running is False
