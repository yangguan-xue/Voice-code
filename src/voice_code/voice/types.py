"""语音模式数据类型 — VoiceState, CommandKind, SpeechSegment 等。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import numpy as np


class VoiceState(StrEnum):
    """语音编排器状态机四态。"""

    SLEEPING = "sleeping"
    LISTENING = "listening"
    WORKING = "working"
    SPEAKING = "speaking"


class CommandKind(StrEnum):
    """指令分类结果。"""

    AGENT_COMMAND = "agent_command"
    CONTROL_COMMAND = "control_command"
    IGNORE = "ignore"


class SupervisorAction(StrEnum):
    """命令助手的结构化调度动作。"""

    REPLY = "reply"
    DISPATCH = "dispatch"
    CONTINUE = "continue"
    ASK_USER = "ask_user"
    FINISH = "finish"
    FAIL = "fail"


@dataclass
class CommandDecision:
    """小模型分类器的输出。"""

    kind: CommandKind
    text: str = ""
    control_name: str = ""


@dataclass
class SpeechSegment:
    """一段 VAD 切分后的完整语音数据。"""

    audio_bytes: bytes
    sample_rate: int = 16000
    duration_seconds: float = 0.0


@dataclass
class VoiceEvent:
    """语音状态机内部事件。"""

    state_from: VoiceState
    state_to: VoiceState
    reason: str = ""
    detail: str = ""


@dataclass
class SupervisorDecision:
    """命令助手对当前轮次的结构化决策。"""

    action: SupervisorAction
    message: str = ""
    task: str = ""
    reason: str = ""
    context_update: str = ""


class SupportsSttClient(Protocol):
    async def transcribe_audio(self, audio_bytes: bytes) -> str: ...
    async def health_check(self) -> bool: ...


class SupportsTtsClient(Protocol):
    async def synthesize_text(self, text: str, seed: int | None = None, **kwargs) -> bytes: ...
    async def synthesize_stream(  # type: ignore[override]
        self, text: str, seed: int | None = None, **kwargs
    ) -> AsyncGenerator[tuple[np.ndarray, int], None]: ...
    async def health_check(self) -> bool: ...


STATUS_TEXT_MAP: dict[VoiceState, str] = {
    VoiceState.SLEEPING: "我已休眠",
    VoiceState.LISTENING: "我在听",
    VoiceState.WORKING: "我正在处理刚才的任务",
    VoiceState.SPEAKING: "我正在播报结果",
}

ALLOWED_CONTROL_NAMES: set[str] = {
    "stop_agent",
    "stop_speaking",
    "report_status",
    "sleep",
    "keep_alive",
}

CONTROL_KEYWORDS: dict[str, str] = {
    "停止": "stop_agent",
    "别读了": "stop_speaking",
    "状态": "report_status",
    "休眠": "sleep",
    "继续": "keep_alive",
}

SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_MS = 30
SEGMENT_MIN_SECONDS = 0.4
SEGMENT_MAX_SECONDS = 40.0
SEGMENT_SILENCE_SECONDS = 0.9
IDLE_TIMEOUT_SECONDS = 600
STT_TIMEOUT_SECONDS = 20
TTS_TIMEOUT_SECONDS = 120
TTS_MAX_TEXT_CHARS = 2000
SUMMARY_MAX_CHARS = 120       # 语音摘要目标字数上限
SUMMARY_FALLBACK_CHARS = 200  # 无摘要模型时的截断字数
SUPERVISOR_MEMORY_LIMIT = 5
SUPERVISOR_CONTEXT_UPDATE_CHARS = 200
SUPERVISOR_MAX_DISPATCHES = 3
SUPERVISOR_MAX_REVIEWS = 4
SUPERVISOR_MAX_TURN_SECONDS = 120.0
WAKE_CONFIRM_TEXT = "我在"
SLEEP_CONFIRM_TEXT = "已休眠，叫我名字可唤醒"
STT_NOT_CLEAR_TEXT = "没听清，请再说一遍"
AGENT_FAILED_TEXT = "任务执行失败，请换个说法"
SUPERVISOR_FAILED_TEXT = "哥哥，我这轮没能稳定完成，麻烦你换个说法或者补一点信息。"
