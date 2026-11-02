from __future__ import annotations

import pytest

from voice_code.desktop_voice.cli import build_parser
from voice_code.desktop_voice.runtime import create_voice_clients
from voice_code.voice.stepfun_client import StepFunASRClient, StepFunTTSClient
from voice_code.voice.stt_client import SttClient
from voice_code.voice.tts_client import VoxcTtsClient


def test_voice_clients_default_to_remote_service_urls() -> None:
    stt_client, tts_client, backend = create_voice_clients()

    assert isinstance(stt_client, SttClient)
    assert isinstance(tts_client, VoxcTtsClient)
    assert stt_client._base_url == "http://localhost:8765/transcribe"
    assert tts_client._base_url == "http://localhost:8775/tts"
    assert backend == "自建语音服务"


def test_voice_clients_accept_complete_remote_service_endpoints() -> None:
    stt_client, tts_client, backend = create_voice_clients(
        voice_provider="remote",
        stt_url="https://stt.example.com/transcribe",
        tts_url="https://tts.example.com/v1/tts",
    )

    assert isinstance(stt_client, SttClient)
    assert isinstance(tts_client, VoxcTtsClient)
    assert stt_client._base_url == "https://stt.example.com/transcribe"
    assert tts_client._base_url == "https://tts.example.com/v1/tts"
    assert backend == "自建语音服务"


def test_voice_clients_send_one_optional_key_to_both_remote_services() -> None:
    stt_client, tts_client, _ = create_voice_clients(
        voice_provider="remote",
        voice_api_key="remote-secret",
    )

    assert stt_client._api_key == "remote-secret"
    assert tts_client._api_key == "remote-secret"


def test_voice_clients_preserve_query_when_appending_endpoint() -> None:
    stt_client, tts_client, _ = create_voice_clients(
        voice_provider="remote",
        stt_url="https://voice.example.com/api?tenant=one",
        tts_url="https://voice.example.com/api?tenant=one#tts",
    )

    assert stt_client._base_url == "https://voice.example.com/api/transcribe?tenant=one"
    assert tts_client._base_url == "https://voice.example.com/api/tts?tenant=one#tts"


def test_voice_clients_use_stepfun_when_api_key_is_configured() -> None:
    stt_client, tts_client, backend = create_voice_clients(
        voice_provider="stepfun",
        stepfun_key="voice-secret",
        stepfun_voice="custom-voice",
    )

    assert isinstance(stt_client, StepFunASRClient)
    assert isinstance(tts_client, StepFunTTSClient)
    assert stt_client._api_key == "voice-secret"
    assert tts_client._api_key == "voice-secret"
    assert tts_client.voice == "custom-voice"
    assert backend == "Step Fun"


def test_voice_clients_require_stepfun_key_when_provider_is_explicit() -> None:
    with pytest.raises(ValueError, match="Step Fun voice provider requires an API key"):
        create_voice_clients(voice_provider="stepfun", stepfun_key="")


def test_cli_keeps_legacy_stepfun_api_key_auto_detection(monkeypatch) -> None:
    monkeypatch.setenv("STEPFUN_API_KEY", "legacy-secret")
    monkeypatch.delenv("REASONING_DESKTOP_VOICE_PROVIDER", raising=False)

    args = build_parser().parse_args([])
    _, _, backend = create_voice_clients(
        voice_provider=args.voice_provider,
        stepfun_key=args.stepfun_key,
    )

    assert args.voice_provider == ""
    assert backend == "Step Fun"
