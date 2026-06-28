"""VAD 分段录音器 — 基于 RMS 分贝 + sounddevice 麦克风采集。"""

from __future__ import annotations

import io
import logging
import math
import struct
import threading
import time
import wave
from collections.abc import Callable

import sounddevice as sd

from voice_code.voice.types import (
    CHANNELS,
    FRAME_MS,
    SAMPLE_RATE,
    SEGMENT_MAX_SECONDS,
    SEGMENT_MIN_SECONDS,
    SEGMENT_SILENCE_SECONDS,
)

logger = logging.getLogger(__name__)

FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
SILENCE_FRAMES = int(SEGMENT_SILENCE_SECONDS * 1000 / FRAME_MS)
MIN_FRAMES = int(SEGMENT_MIN_SECONDS * 1000 / FRAME_MS)
MAX_FRAMES = int(SEGMENT_MAX_SECONDS * 1000 / FRAME_MS)
SPEECH_ONSET_FRAMES = 5  # 连续 5 帧 (150ms) 语音才确认说话开始


class SegmentRecorder:
    """VAD 分段录音器 — 管理麦克风采集 + RMS 分贝语音检测。

    使用 sounddevice InputStream 在后台线程持续读取音频帧。
    每帧计算 RMS 分贝值，高于阈值为语音、低于为静音。
    连续 5 帧语音确认 onset，0.9s 静音切段。
    """

    def __init__(self, aggressiveness: int = 2, min_db: float = -45.0) -> None:
        """初始化分段录音器。

        Args:
            aggressiveness: 保留参数（兼容旧接口），当前无实际效果。
            min_db: 语音最低分贝阈值 (-60~0)，低于此值视为静音。
                    默认 -45dB，嘈杂环境可调到 -35dB。
        """
        self._min_db = min_db

        self._recording = False
        self._frames: list[bytes] = []
        self._silence_count = 0
        self._total_frames = 0
        self._speech_start_time: float = 0.0
        self._consecutive_speech = 0
        self._pending_onset: list[bytes] = []
        self._lock = threading.Lock()

        self._callback: Callable[[bytes, float], None] | None = None
        self._raw_frame_callback: Callable[[bytes], None] | None = None

        self._stream: sd.InputStream | None = None
        self._mic_running = False
        self._mic_thread: threading.Thread | None = None
        self._frame_queue: list[bytes] = []
        self._queue_lock = threading.Lock()
        self._queue_event = threading.Event()

        logger.info(
            "SegmentRecorder initialized: sample_rate=%d, frame_ms=%d, "
            "min_s=%.1f, max_s=%.1f, silence_s=%.1f",
            SAMPLE_RATE, FRAME_MS, SEGMENT_MIN_SECONDS,
            SEGMENT_MAX_SECONDS, SEGMENT_SILENCE_SECONDS,
        )

    def on_segment(self, callback: Callable[[bytes, float], None]) -> None:
        """注册分段完成回调，参数为 (wav_bytes, speech_start_time_monotonic)。"""
        self._callback = callback

    def on_raw_frame(self, callback: Callable[[bytes], None]) -> None:
        """注册原始音频帧回调 — 每帧 (30ms PCM) 触发，用于唤醒词等场景。"""
        self._raw_frame_callback = callback

    # ---- Microphone stream ----

    def open_mic(self) -> None:
        """打开麦克风采集流，启动后台处理线程。"""
        if self._mic_running:
            return

        self._mic_running = True
        self._queue_event.clear()

        def _audio_callback(indata: bytes, frames: int, time_info: object, status: int) -> None:
            if status:
                logger.warning("Mic status: %s", status)
            with self._queue_lock:
                self._frame_queue.append(bytes(indata))
            self._queue_event.set()

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            callback=_audio_callback,
        )
        self._stream.start()

        self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True, name="mic-loop")
        self._mic_thread.start()
        logger.info("SegmentRecorder: mic stream opened")

    def close_mic(self) -> None:
        """关闭麦克风流和后台线程。"""
        self._mic_running = False
        self._queue_event.set()
        if self._mic_thread is not None:
            self._mic_thread.join(timeout=2.0)
            self._mic_thread = None
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._queue_lock:
            self._frame_queue.clear()
        self._recording = False
        logger.info("SegmentRecorder: mic stream closed")

    def _mic_loop(self) -> None:
        """后台线程：从队列取帧 → 原始回调 + VAD 处理。"""
        while self._mic_running:
            self._queue_event.wait(timeout=0.5)
            self._queue_event.clear()

            frame: bytes | None = None
            with self._queue_lock:
                if self._frame_queue:
                    frame = self._frame_queue.pop(0)
                    # 防止队列堆积：一次最多处理 200 帧 (6s)
                    self._frame_queue = self._frame_queue[-200:]

            if frame is None:
                continue

            # 原始帧回调（用于唤醒词等外部消费者）
            if self._raw_frame_callback is not None:
                try:
                    self._raw_frame_callback(frame)
                except Exception:
                    logger.exception("SegmentRecorder: raw frame callback error")

            # VAD 分段处理
            if self._recording:
                self.process_frame(frame)

    # ---- VAD segment recording ----

    def start(self) -> None:
        """开始 VAD 录音。"""
        with self._lock:
            self._recording = True
            self._frames.clear()
            self._silence_count = 0
            self._total_frames = 0
            self._speech_start_time = 0.0
            self._consecutive_speech = 0
            self._pending_onset.clear()
        logger.debug("SegmentRecorder: VAD recording started")

    def stop(self) -> None:
        """停止 VAD 录音。"""
        with self._lock:
            self._recording = False
        logger.debug("SegmentRecorder: VAD recording stopped")

    def process_frame(self, frame_bytes: bytes) -> bool:
        """处理一帧音频（30ms PCM），VAD + 静音切段。

        Returns:
            True 如果完成了一个分段。
        """
        with self._lock:
            if not self._recording:
                return False

            if len(frame_bytes) == FRAME_SAMPLES * 2:
                db_fs = self._rms_db(frame_bytes)
                is_speech = db_fs >= self._min_db
            else:
                db_fs = -100.0
                is_speech = False

            # 每 30 帧 (~1s) 打印 dB 供调参
            if self._total_frames % 30 == 0 and self._total_frames > 0:
                logger.info("VAD dB=%.1f speech=%s frames=%d silence=%d",
                            db_fs, is_speech, self._total_frames, self._silence_count)

            if is_speech:
                self._consecutive_speech += 1
                if self._consecutive_speech < SPEECH_ONSET_FRAMES:
                    # 还没确认说话，缓冲帧
                    self._pending_onset.append(frame_bytes)
                else:
                    # 确认说话：先写入缓冲帧，再写当前帧
                    if self._pending_onset:
                        if not self._frames:
                            self._speech_start_time = time.monotonic()
                            logger.info("VAD speech onset (debounced) at dB=%.1f", db_fs)
                        self._frames.extend(self._pending_onset)
                        self._total_frames += len(self._pending_onset)
                        self._pending_onset.clear()
                    if not self._frames:
                        self._speech_start_time = time.monotonic()
                        logger.info("VAD speech onset at dB=%.1f", db_fs)
                    self._frames.append(frame_bytes)
                    self._silence_count = 0
                    self._total_frames += 1
            else:
                # 非语音：清空 debounce
                self._consecutive_speech = 0
                self._pending_onset.clear()
                if self._frames:
                    # 已经在说话，此帧计为静音
                    self._frames.append(frame_bytes)
                    self._silence_count += 1
                    self._total_frames += 1

            if not self._frames:
                return False

            too_long = self._total_frames >= MAX_FRAMES
            silence_done = self._silence_count >= SILENCE_FRAMES

            if silence_done or too_long:
                if self._total_frames >= MIN_FRAMES:
                    segment_bytes = self._build_wav()
                    self._frames.clear()
                    self._silence_count = 0
                    self._total_frames = 0
                    self._consecutive_speech = 0
                    self._pending_onset.clear()
                    if self._callback:
                        try:
                            self._callback(segment_bytes, self._speech_start_time)
                        except Exception:
                            logger.exception("SegmentRecorder: callback error")
                    return True
                else:
                    logger.debug(
                        "SegmentRecorder: discarding short segment (%d frames)",
                        self._total_frames,
                    )
                    self._frames.clear()
                    self._silence_count = 0
                    self._total_frames = 0
                    self._consecutive_speech = 0
                    self._pending_onset.clear()

            return False

    def get_current_segment(self) -> bytes | None:
        """获取当前未完成的录音片段（用于中断时保存）。"""
        with self._lock:
            if not self._frames:
                return None
            return self._build_wav()

    def drain_queue(self) -> None:
        """清空帧队列 — 播报前调用，丢弃已录到的 TTS 回音。"""
        with self._queue_lock:
            self._frame_queue.clear()
        with self._lock:
            self._frames.clear()
            self._silence_count = 0
            self._total_frames = 0
            self._consecutive_speech = 0
            self._pending_onset.clear()

    def is_speech_active(self) -> bool:
        """返回当前是否有语音活动。"""
        with self._lock:
            return len(self._frames) > 0

    @staticmethod
    def _rms_db(frame_bytes: bytes) -> float:
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

    def _build_wav(self) -> bytes:
        """将累积的帧组成 WAV 文件。"""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(self._frames))
        return buf.getvalue()
