"""Step Fun 语音 API 客户端 — ASR (语音识别) + TTS (语音合成)。

通过 Step Plan 接口接入，替代远端自建 STT/TTS 服务。
API 文档: https://platform.stepfun.com/docs
域名: https://api.stepfun.com/step_plan/v1
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import wave

import httpx

logger = logging.getLogger(__name__)

STEPFUN_BASE = "https://api.stepfun.com/step_plan/v1"
STEPFUN_API_KEY_ENV = "STEPFUN_API_KEY"

ASR_MODEL = "stepaudio-2.5-asr"
TTS_MODEL = "stepaudio-2.5-tts"
DEFAULT_VOICE = "cixingnansheng"
DEFAULT_TTS_FORMAT = "wav"

# ---- ASR ----

# Step Fun ASR 流式返回 SSE 事件，非流式调用需要从 SSE 中拼接完整文本。
# 当前 Step Plan 下仅支持 HTTP + SSE 方式。

_SSE_DATA_RE = re.compile(r"^data:\s*(.*)$")


class StepFunASRClient:
    """Step Fun 语音识别 (STT) 客户端。

    覆盖 SttClient 的同名接口，可无缝替换。
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv(STEPFUN_API_KEY_ENV, "")
        self._url = f"{STEPFUN_BASE}/audio/asr/sse"
        if not self._api_key:
            logger.warning("StepFunASRClient: no API key set (env STEPFUN_API_KEY)")
        logger.info("StepFunASRClient initialized: %s", self._url)

    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        """将 wav bytes 发给 Step Fun ASR，返回识别文本。

        输入为 16kHz mono 16bit WAV，内部转为 PCM base64。

        Raises:
            RuntimeError: 请求失败或识别为空。
        """
        # 剥离 WAV 头 → 原始 PCM
        pcm = _wav_to_pcm(audio_bytes)
        pcm_b64 = base64.b64encode(pcm).decode("ascii")

        body = {
            "audio": {
                "data": pcm_b64,
                "input": {
                    "transcription": {
                        "model": ASR_MODEL,
                        "language": "zh",
                        "enable_itn": True,
                    },
                    "format": {
                        "type": "pcm",
                        "codec": "pcm_s16le",
                        "rate": 16000,
                        "bits": 16,
                        "channel": 1,
                    },
                },
            }
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                response = await client.post(
                    self._url,
                    json=body,
                    headers=headers,
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            raise RuntimeError("stt request timed out") from None
        except httpx.HTTPError as e:
            raise RuntimeError(f"stt request failed: {e}") from None

        text = _parse_asr_sse(response.text)
        if not text.strip():
            raise RuntimeError("stt returned empty text")

        logger.info("StepFun ASR (%d chars): %s", len(text), text[:100])
        return text.strip()

    async def health_check(self) -> bool:
        return bool(self._api_key)


# ---- TTS ----


class StepFunTTSClient:
    """Step Fun 语音合成 (TTS) 客户端。

    覆盖 TtsClient 的同名接口，可无缝替换。
    """

    def __init__(
        self,
        api_key: str | None = None,
        voice: str = DEFAULT_VOICE,
        response_format: str = DEFAULT_TTS_FORMAT,
    ) -> None:
        self._api_key = api_key or os.getenv(STEPFUN_API_KEY_ENV, "")
        self._url = f"{STEPFUN_BASE}/audio/speech"
        self._voice = voice
        self._format = response_format
        if not self._api_key:
            logger.warning("StepFunTTSClient: no API key set (env STEPFUN_API_KEY)")
        logger.info(
            "StepFunTTSClient initialized: %s, voice=%s, format=%s",
            self._url,
            self._voice,
            self._format,
        )

    @property
    def voice(self) -> str:
        return self._voice

    @voice.setter
    def voice(self, name: str) -> None:
        self._voice = name

    async def synthesize_text(self, text: str, seed: int | None = None) -> bytes:
        """将文本发给 Step Fun TTS，返回 wav bytes。

        Raises:
            RuntimeError: 请求失败或返回音频无效。
        """
        if not text or not text.strip():
            raise RuntimeError("tts text is empty")

        body = {
            "model": TTS_MODEL,
            "input": text.strip(),
            "voice": self._voice,
            "response_format": self._format,
        }
        if seed is not None:
            logger.debug("StepFun TTS ignores seed=%s", seed)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                response = await client.post(
                    self._url,
                    json=body,
                    headers=headers,
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            raise RuntimeError("tts request timed out") from None
        except httpx.HTTPError as e:
            raise RuntimeError(f"tts request failed: {e}") from None

        audio_bytes = response.content
        if not audio_bytes or len(audio_bytes) < 44:
            raise RuntimeError(f"tts returned invalid audio ({len(audio_bytes)} bytes)")

        logger.info("StepFun TTS: %s -> %d bytes", text[:60], len(audio_bytes))
        return audio_bytes

    async def health_check(self) -> bool:
        return bool(self._api_key)


# ---- Helpers ----


def _wav_to_pcm(wav_bytes: bytes) -> bytes:
    """从标准 WAV 中提取原始 PCM 数据（剥离 44 字节头）。"""
    if len(wav_bytes) < 44:
        return wav_bytes
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        # 校验参数
        if wf.getsampwidth() != 2:
            raise RuntimeError(
                f"wav sample width must be 2 (16-bit), got {wf.getsampwidth()}"
            )
        if wf.getnchannels() != 1:
            raise RuntimeError(f"wav must be mono, got {wf.getnchannels()} channels")
        if wf.getframerate() != 16000:
            raise RuntimeError(f"wav sample rate must be 16000, got {wf.getframerate()}")
    # wave 模块不会直接给 PCM，切掉 WAV 头（前 44 字节或直到 data chunk）
    # 用标准 RIFF WAV 头大小
    return wav_bytes[44:]


def _parse_asr_sse(sse_text: str) -> str:
    """解析 Step Fun ASR 的 SSE 事件流，提取完整文本。

    事件格式:
        data: {"text": "部分文本", ...}
        event: result
    """
    parts: list[str] = []
    for line in sse_text.split("\n"):
        match = _SSE_DATA_RE.match(line.strip())
        if not match:
            continue
        data_str = match.group(1)
        if data_str in ("[DONE]", ""):
            continue
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        chunk = event.get("text", "") or event.get("transcription", {}).get("text", "")
        if isinstance(chunk, str):
            parts.append(chunk)
    return "".join(parts)


# ---- CLI Test ----


if __name__ == "__main__":
    import asyncio
    import sys

    logging.basicConfig(level=logging.INFO)

    async def main() -> None:
        api_key = os.getenv(STEPFUN_API_KEY_ENV)
        if not api_key:
            print("请设置环境变量 STEPFUN_API_KEY")
            sys.exit(1)

        # 测试 TTS
        tts = StepFunTTSClient(api_key=api_key)
        print("=== TTS 测试 ===")
        audio = await tts.synthesize_text("你好，我是语音助手，正在通过 Step Fun 服务合成语音。")
        path = "/tmp/stepfun_test.wav"
        with open(path, "wb") as f:
            f.write(audio)
        print(f"TTS OK -> {path} ({len(audio)} bytes)")

        # 测试 ASR（用一段合成音频自测或读取文件）
        print("\n=== ASR 测试 ===")
        asr = StepFunASRClient(api_key=api_key)
        if len(sys.argv) > 1:
            with open(sys.argv[1], "rb") as f:
                wav_bytes = f.read()
            text = await asr.transcribe_audio(wav_bytes)
            print(f"ASR: {text}")

    asyncio.run(main())
