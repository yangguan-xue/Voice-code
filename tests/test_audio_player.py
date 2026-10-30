from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest

from voice_code.voice.audio_player import AudioPlayer


class _FakeOutputStream:
    instances: list[_FakeOutputStream] = []

    def __init__(self, **_: object) -> None:
        self.started = False
        self.closed = False
        self.writes = 0
        self.write_started = threading.Event()
        self.release_write = threading.Event()
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started = True

    def write(self, _chunk: np.ndarray) -> None:
        self.write_started.set()
        self.release_write.wait(timeout=1)
        self.writes += 1

    def stop(self) -> None:
        self.closed = True

    def abort(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stop_does_not_silence_the_next_stream(monkeypatch) -> None:
    _FakeOutputStream.instances.clear()
    class FakeSounddevice:
        OutputStream = _FakeOutputStream

        @staticmethod
        def stop() -> None:
            pass

    monkeypatch.setattr(
        "voice_code.voice.audio_player._sounddevice",
        lambda: FakeSounddevice,
    )
    player = AudioPlayer()
    player.stop()
    player.begin_stream()

    await player.play_chunk(np.zeros(32, dtype=np.float32), 16_000)

    assert _FakeOutputStream.instances[-1].writes == 1
    await player.close_stream()


@pytest.mark.asyncio
async def test_stream_write_runs_outside_the_event_loop(monkeypatch) -> None:
    _FakeOutputStream.instances.clear()
    class FakeSounddevice:
        OutputStream = _FakeOutputStream

        @staticmethod
        def stop() -> None:
            pass

    monkeypatch.setattr(
        "voice_code.voice.audio_player._sounddevice",
        lambda: FakeSounddevice,
    )
    player = AudioPlayer()
    player.begin_stream()
    write_task = asyncio.create_task(
        player.play_chunk(np.zeros(32, dtype=np.float32), 16_000)
    )

    await asyncio.sleep(0)
    await asyncio.to_thread(_FakeOutputStream.instances[0].write_started.wait, 1)
    await asyncio.sleep(0)
    assert not write_task.done()

    _FakeOutputStream.instances[0].release_write.set()
    await write_task
    await player.close_stream()
