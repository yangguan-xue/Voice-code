from __future__ import annotations

import httpx
import pytest

from voice_code.voice.stt_client import SttClient
from voice_code.voice.tts_client import TtsClient


class _Response:
    def __init__(self, *, json_data: dict[str, str] | None = None) -> None:
        self._json_data = json_data
        self.headers = {"content-type": "audio/wav"}
        self.content = b"RIFF" + (b"\x00" * 128)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return self._json_data or {"text": "识别结果"}


class _AsyncClient:
    response: _Response
    requests: list[tuple[str, dict[str, str]]]

    def __init__(self, **_kwargs: object) -> None:
        self.response = _Response()
        self.requests = []

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> _Response:
        self.requests.append((url, kwargs["headers"]))  # type: ignore[arg-type]
        return self.response


@pytest.mark.asyncio
async def test_stt_client_sends_optional_bearer_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _AsyncClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)

    text = await SttClient(
        base_url="https://voice.example.com/transcribe",
        api_key="remote-secret",
    ).transcribe_audio(b"wav")

    assert text == "识别结果"
    assert client.requests[0][1] == {
        "Content-Type": "audio/wav",
        "Authorization": "Bearer remote-secret",
    }


@pytest.mark.asyncio
async def test_tts_client_omits_authorization_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _AsyncClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)

    audio = await TtsClient(base_url="https://voice.example.com/tts").synthesize_text("你好")

    assert audio.startswith(b"RIFF")
    assert client.requests[0][1] == {"Content-Type": "application/json"}
