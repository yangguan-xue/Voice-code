"""唤醒词检测器 — 本地关键词检测。"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class WakeWordDetector:
    """唤醒词检测器 V0。

    V0 采用 STT 流式关键词匹配方案（不是专用唤醒词引擎）：
    - 在 sleeping 状态下持续监听短音频段
    - 通过轻量 STT 识别文本
    - 文本中匹配唤醒词关键词

    这是 V0 折衷方案，不引入 porcupine/snowboy 等额外依赖。
    后续版本可替换为专用 Wake Word Engine。
    """

    DEFAULT_WAKE_WORDS = ["codex", "扣的", "扣得", "代码", "开发助手"]

    def __init__(self, wake_words: list[str] | None = None) -> None:
        """初始化唤醒词检测器。

        Args:
            wake_words: 唤醒词列表，默认 DEFAULT_WAKE_WORDS。
        """
        self._wake_words = [w.lower() for w in (wake_words or self.DEFAULT_WAKE_WORDS)]
        self._on_wake_callbacks: list[Callable[[], None]] = []
        logger.info("WakeWordDetector initialized: %s", self._wake_words)

    def on_wake(self, callback: Callable[[], None]) -> None:
        """注册唤醒回调。"""
        self._on_wake_callbacks.append(callback)

    async def detect_from_bytes(self, audio_bytes: bytes, stt_text: str = "") -> bool:
        """检测音频段是否含唤醒词。

        Orchestrator 已在外部完成 STT 转写，本方法只需做关键词匹配。

        Args:
            audio_bytes: 短音频 wav bytes。
            stt_text: STT 已转写的文本（由 orchestrator 传入）。

        Returns:
            True 如果检测到唤醒词。
        """
        text = stt_text.lower().strip() if stt_text else ""
        if not text:
            return False

        for word in self._wake_words:
            # 精确子串匹配
            if word in text:
                logger.info("WakeWordDetector: exact match: '%s' in '%s'", word, text)
                for cb in self._on_wake_callbacks:
                    try:
                        cb()
                    except Exception:
                        logger.exception("WakeWordDetector: callback error")
                return True
            # 前缀模糊匹配：唤醒词 >= 3 字时，前 3 字匹配即触发
            if len(word) >= 3:
                prefix = word[:3]
                if prefix in text:
                    logger.info(
                        "WakeWordDetector: prefix match: '%s' (prefix='%s') in '%s'",
                        word, prefix, text,
                    )
                    for cb in self._on_wake_callbacks:
                        try:
                            cb()
                        except Exception:
                            logger.exception("WakeWordDetector: callback error")
                    return True

        return False
