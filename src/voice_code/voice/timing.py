"""语音流水线耗时追踪 — 从麦克风收声到播报完成的每阶段计时。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TurnTiming:
    """单轮语音交互的各阶段耗时（秒）。"""

    # 麦克风采集 → VAD 切段
    vad_wall_seconds: float = 0.0

    # 音频发送 → STT 文本返回
    stt_wall_seconds: float = 0.0

    # 分类器判定
    classify_wall_seconds: float = 0.0

    # agent_loop 完整执行
    agent_wall_seconds: float = 0.0

    # 文本发送 → TTS 音频返回
    tts_wall_seconds: float = 0.0

    # 音频播放
    playback_wall_seconds: float = 0.0

    # 额外信息
    stt_text_len: int = 0
    agent_result_len: int = 0
    tts_audio_bytes: int = 0

    @property
    def total_wall_seconds(self) -> float:
        return (
            self.vad_wall_seconds
            + self.stt_wall_seconds
            + self.classify_wall_seconds
            + self.agent_wall_seconds
            + self.tts_wall_seconds
            + self.playback_wall_seconds
        )

    def format_summary(self) -> str:
        parts = [
            f"VAD     {self.vad_wall_seconds:6.2f}s",
            f"STT     {self.stt_wall_seconds:6.2f}s  ({self.stt_text_len} chars)",
            f"分类    {self.classify_wall_seconds:6.2f}s",
            f"Agent   {self.agent_wall_seconds:6.2f}s  ({self.agent_result_len} chars)",
            f"TTS     {self.tts_wall_seconds:6.2f}s  ({self.tts_audio_bytes} bytes)",
            f"播放    {self.playback_wall_seconds:6.2f}s",
        ]
        parts.append(f"─── 合计 {self.total_wall_seconds:6.2f}s")
        return "\n".join(parts)


@dataclass
class TimingCollector:
    """流水线计时收集器。"""

    turns: list[TurnTiming] = field(default_factory=list)
    _current: TurnTiming = field(default_factory=TurnTiming)
    _stage_start: float = 0.0

    def start_stage(self) -> None:
        self._stage_start = time.monotonic()

    def end_vad(self, text: str = "") -> None:
        self._current.vad_wall_seconds = time.monotonic() - self._stage_start
        self._current.stt_text_len = len(text)

    def end_stt(self) -> None:
        self._current.stt_wall_seconds = time.monotonic() - self._stage_start

    def end_classify(self) -> None:
        self._current.classify_wall_seconds = time.monotonic() - self._stage_start

    def end_agent(self, result_len: int = 0) -> None:
        self._current.agent_wall_seconds = time.monotonic() - self._stage_start
        self._current.agent_result_len = result_len

    def end_tts(self, audio_bytes: int = 0) -> None:
        self._current.tts_wall_seconds = time.monotonic() - self._stage_start
        self._current.tts_audio_bytes = audio_bytes

    def end_playback(self) -> None:
        self._current.playback_wall_seconds = time.monotonic() - self._stage_start

    def finish_turn(self) -> TurnTiming:
        turn = self._current
        self.turns.append(turn)
        self._current = TurnTiming()
        return turn
