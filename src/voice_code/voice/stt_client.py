"""远端 STT HTTP 客户端 — 调用 SenseVoiceSmall 服务。"""

from __future__ import annotations

import json
import logging
import os

import httpx

from voice_code.voice.types import STT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

DEFAULT_STT_URL = "http://localhost:8765/transcribe"


class SttClient:
    """远端语音转文字 HTTP 客户端。

    服务端: FunASR SenseVoiceSmall, POST /transcribe.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or os.getenv("STT_BASE_URL") or DEFAULT_STT_URL).rstrip("/")
        self._timeout = httpx.Timeout(STT_TIMEOUT_SECONDS)
        logger.info("SttClient initialized: %s", self._base_url)

    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        """将 wav bytes 发给 STT 服务，返回识别文本。

        Raises:
            RuntimeError: 请求失败或服务返回错误。
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._base_url,
                    content=audio_bytes,
                    headers={"Content-Type": "audio/wav"},
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.error("STT request timed out")
            raise RuntimeError("stt request timed out") from None
        except httpx.HTTPError:
            logger.error("STT HTTP request failed")
            raise RuntimeError("stt request failed") from None

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError):
            logger.error("STT response not valid JSON")
            raise RuntimeError("stt response parse failed") from None

        if "error" in data:
            logger.error("STT service returned an error")
            raise RuntimeError("stt service error")

        text = data.get("text", "")
        if not isinstance(text, str) or not text.strip():
            logger.warning("STT returned empty text")
            raise RuntimeError("stt returned empty text")

        logger.info("STT completed (%d chars)", len(text))
        return text.strip()

    async def health_check(self) -> bool:
        """检查 STT 服务是否可用。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url.rsplit('/', 1)[0]}/health")
                return resp.status_code == 200
        except Exception:
            return False
