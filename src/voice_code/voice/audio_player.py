"""本地音频播放器 — 支持 WAV 播放和流式 PCM 播放。"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import wave
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def _sounddevice() -> Any:
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - depends on host audio libraries
        raise RuntimeError(
            "sounddevice is unavailable. Install PortAudio or use a non-audio test/runtime path."
        ) from exc
    return sd


class AudioPlayer:
    """音频播放器。

    支持完整 WAV 播放（play_wav_bytes）和流式 PCM 增量播放（play_stream）。
    """

    def __init__(self) -> None:
        self._stream: Any | None = None
        self._playing = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def play_wav_bytes(self, audio_bytes: bytes) -> None:
        """播放完整 WAV bytes（阻塞）。"""
        if not audio_bytes or len(audio_bytes) < 44:
            logger.warning("AudioPlayer: empty or invalid wav data")
            return

        self.begin_stream()
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                sample_rate = wf.getframerate()
                channels = wf.getnchannels()
                dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
                sample_width = wf.getsampwidth()
                dtype = dtype_map.get(sample_width, np.int16)
                raw = wf.readframes(wf.getnframes())
                samples = np.frombuffer(raw, dtype=dtype)
                if channels > 1:
                    multi = samples.reshape(-1, channels)
                    audio_data = multi.mean(axis=1).astype(dtype)
                else:
                    audio_data = samples
        except Exception as e:
            logger.error("AudioPlayer: failed to decode wav")
            raise RuntimeError(f"audio decode failed: {e}") from e

        with self._lock:
            self._playing = True

        try:
            sd = _sounddevice()
            sd.play(audio_data, samplerate=sample_rate)
            sd.wait()
        except Exception as e:
            logger.error("AudioPlayer: playback error")
            raise RuntimeError(f"audio playback failed: {e}") from e
        finally:
            with self._lock:
                self._playing = False

    async def play_stream(
        self,
        stream: AsyncGenerator[tuple[np.ndarray, int], None],
    ) -> None:
        """流式播放 PCM float32 chunks。

        Args:
            stream: AsyncGenerator，yield (audio_chunk_1d_float32, sample_rate)
        """
        self.begin_stream()

        first = True
        output_stream: Any | None = None
        total_samples = 0

        try:
            async for audio_chunk, sample_rate in stream:
                if self._stop_event.is_set():
                    break

                if first:
                    logger.info("AudioPlayer: starting stream playback sr=%d", sample_rate)
                    sd = _sounddevice()
                    output_stream = sd.OutputStream(
                        samplerate=sample_rate,
                        channels=1,
                        dtype="float32",
                        blocksize=4096,
                    )
                    output_stream.start()
                    first = False

                with self._lock:
                    self._playing = True

                if output_stream is not None:
                    chunk = np.ascontiguousarray(audio_chunk, dtype=np.float32)
                    if chunk.ndim == 1:
                        chunk = chunk.reshape(-1, 1)
                    await asyncio.to_thread(output_stream.write, chunk)
                    total_samples += len(chunk)

        except Exception:
            logger.error("AudioPlayer: stream playback error")
            raise
        finally:
            if output_stream is not None:
                await asyncio.to_thread(self._close_output_stream, output_stream)
            with self._lock:
                self._playing = False
            duration = total_samples / (sample_rate if not first else 48000)
            logger.info("AudioPlayer: stream done, played %.2fs", duration)

    def begin_stream(self) -> None:
        """Prepare a new playback generation after a previous stop."""
        self._stop_current()
        with self._lock:
            previous_stream = self._stream
            self._stream = None
            self._playing = False
            self._stop_event.clear()
        if previous_stream is not None:
            self._close_output_stream(previous_stream)

    def stop(self) -> None:
        """停止当前播放。"""
        self._stop_event.set()
        self._stop_current()
        with self._lock:
            stream = self._stream
            self._stream = None
            self._playing = False
        if stream is not None:
            self._close_output_stream(stream, abort=True)

    def is_playing(self) -> bool:
        """返回是否正在播放。"""
        with self._lock:
            return self._playing

    def stop_requested(self) -> bool:
        """Return whether the current playback generation was stopped."""
        return self._stop_event.is_set()

    async def play_chunk(self, audio_chunk: np.ndarray, sample_rate: int) -> bool:
        """向当前流写入一个音频 chunk，不阻塞事件循环。"""
        if self._stop_event.is_set():
            return False
        chunk = np.ascontiguousarray(audio_chunk, dtype=np.float32)
        if chunk.ndim == 1:
            chunk = chunk.reshape(-1, 1)
        if self._stream is None:
            sd = _sounddevice()
            self._stream = sd.OutputStream(
                samplerate=sample_rate, channels=1, dtype="float32", blocksize=4096
            )
            self._stream.start()
        with self._lock:
            self._playing = True
            stream = self._stream
        try:
            await asyncio.to_thread(stream.write, chunk)
        except Exception:
            if self._stop_event.is_set():
                return False
            raise
        return not self._stop_event.is_set()

    async def close_stream(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            await asyncio.to_thread(self._close_output_stream, stream)
        with self._lock:
            self._playing = False

    def _stop_current(self) -> None:
        try:
            sd = _sounddevice()
            sd.stop()
        except Exception:
            pass

    @staticmethod
    def _close_output_stream(stream: Any, *, abort: bool = False) -> None:
        try:
            try:
                if abort and hasattr(stream, "abort"):
                    stream.abort()
                else:
                    stream.stop()
            except Exception:
                logger.debug("AudioPlayer: output stream stop failed", exc_info=True)
        finally:
            try:
                stream.close()
            except Exception:
                logger.debug("AudioPlayer: output stream close failed", exc_info=True)
