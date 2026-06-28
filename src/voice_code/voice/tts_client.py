"""远端 TTS HTTP 客户端 — 支持流式合成。"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncGenerator

import httpx
import numpy as np

from voice_code.voice.types import TTS_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

DEFAULT_TTS_URL = "http://localhost:8775/v1/tts"


class TtsClient:
    """远端文字转语音 HTTP 客户端。

    POST /tts body: {"text": "...", "seed": 1, "streaming": true/false}
    返回 WAV bytes（非流式）或流式 PCM chunk。
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or os.getenv("TTS_BASE_URL") or DEFAULT_TTS_URL).rstrip("/")
        self._timeout = httpx.Timeout(TTS_TIMEOUT_SECONDS)
        logger.info("TtsClient initialized: %s", self._base_url)

    async def synthesize_text(self, text: str, seed: int | None = None, **kwargs) -> bytes:
        """非流式合成，返回完整 WAV bytes。"""
        if not text or not text.strip():
            raise RuntimeError("tts text is empty")

        text = re.sub(r'[\U0001F300-\U0001F9FF`*_~#]', '', text).strip()
        body: dict = {"text": text.strip(), "streaming": False}
        if seed is not None:
            body["seed"] = seed
        body.update(kwargs)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._base_url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.exception("TTS request timed out")
            raise RuntimeError("tts request timed out") from None
        except httpx.HTTPError as e:
            logger.exception("TTS HTTP request failed")
            raise RuntimeError(f"tts request failed: {e}") from None

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError):
                logger.error("TTS error response not valid JSON")
                raise RuntimeError("tts error response parse failed") from None
            if "error" in data:
                err = data["error"]
                logger.error("TTS service error: %s", err)
                raise RuntimeError(f"tts service error: {err.get('message', 'unknown')}")

        audio_bytes = response.content
        if not audio_bytes or len(audio_bytes) < 44:
            logger.error("TTS returned empty or too-small audio (%d bytes)", len(audio_bytes))
            raise RuntimeError("tts returned invalid audio")

        logger.info("TTS result: %d bytes", len(audio_bytes))
        return audio_bytes

    async def synthesize_stream(
        self,
        text: str,
        seed: int | None = None,
        **kwargs,
    ) -> AsyncGenerator[tuple[np.ndarray, int], None]:
        """流式合成，逐个 yield (PCM float32 1D array, sample_rate)。

        协议：4-byte big-endian uint32 帧长度，后跟 float32 PCM 数据。
        0 长度 = 流结束。sample_rate 默认 48000（VoxCPM2）。
        """
        if not text or not text.strip():
            raise RuntimeError("tts text is empty")

        text = re.sub(r'[\U0001F300-\U0001F9FF`*_~#]', '', text).strip()
        body: dict = {"text": text.strip(), "streaming": True}
        if seed is not None:
            body["seed"] = seed
        body.update(kwargs)

        sample_rate = 48000
        timeout = httpx.Timeout(TTS_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                self._base_url,
                json=body,
                headers={"Content-Type": "application/json"},
            ) as response:
                response.raise_for_status()

                buf = bytearray()
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    while len(buf) >= 4:
                        frame_len = int.from_bytes(buf[:4], "big")
                        if frame_len == 0:
                            buf.clear()
                            break
                        if len(buf) < 4 + frame_len:
                            break
                        arr = np.frombuffer(buf[4:4 + frame_len], dtype=np.float32).copy()
                        buf = buf[4 + frame_len:]
                        if len(arr) > 0:
                            yield arr, sample_rate

                logger.info("TTS stream done, sr=%d", sample_rate)

    async def health_check(self) -> bool:
        """检查 TTS 服务是否可用。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url.rsplit('/', 1)[0]}/health")
                return resp.status_code == 200
        except Exception:
            return False


class VoxcTtsClient(TtsClient):
    """VoxCPM2 TTS 客户端 — 兼容旧代码。"""
