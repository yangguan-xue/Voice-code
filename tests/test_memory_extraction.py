from __future__ import annotations

import json

import httpx
import pytest

from voice_code.memory.candidates import ExtractionMode, SensitivityDecision
from voice_code.memory.config import ExtractionConfig, MemoryRagConfig
from voice_code.memory.extraction import (
    ExtractionInputRejectedError,
    ExtractionResponseError,
    OpenAICompatibleExtractionProvider,
    TurnExtractionInput,
)
from voice_code.memory.rag_models import MemoryKind, MemoryScope


def _config(**overrides) -> ExtractionConfig:
    values = {
        "base_url": "https://extractor.example/v1",
        "api_key": "private-extraction-key",
        "model": "memory-extractor",
        "mode": ExtractionMode.SHADOW,
        "timeout_seconds": 3.0,
        "min_confidence": 0.9,
        "max_candidates_per_turn": 2,
    }
    values.update(overrides)
    return ExtractionConfig(**values)


def _turn(**overrides) -> TurnExtractionInput:
    values = {
        "user_text": "以后回答先给结论",
        "assistant_text": "明白，我会先给结论。",
        "source_session_id": "session-1",
        "source_turn_id": "turn-1",
        "project_available": True,
    }
    values.update(overrides)
    return TurnExtractionInput(**values)


def test_extraction_defaults_off_without_provider_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("REASONING_HOME", str(tmp_path))
    for name in (
        "REASONING_MEMORY_EXTRACTION_MODE",
        "MEMORY_EXTRACTION_BASE_URL",
        "MEMORY_EXTRACTION_API_KEY",
        "MEMORY_EXTRACTION_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    config = MemoryRagConfig.from_env()

    assert config.extraction_mode is ExtractionMode.OFF
    assert config.extraction is None


def test_extraction_config_hides_secret_and_requires_credentials_when_enabled(monkeypatch):
    assert "private-extraction-key" not in repr(_config())
    monkeypatch.setenv("REASONING_MEMORY_EXTRACTION_MODE", "shadow")
    monkeypatch.delenv("MEMORY_EXTRACTION_BASE_URL", raising=False)
    monkeypatch.delenv("MEMORY_EXTRACTION_API_KEY", raising=False)

    with pytest.raises(ValueError, match="base URL and API key"):
        MemoryRagConfig.from_env()


@pytest.mark.asyncio
async def test_provider_sends_bounded_turn_and_parses_strict_candidates():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "schema_version": 1,
                            "candidates": [{
                                "kind": "preference",
                                "scope": "user",
                                "content": "回答时先给结论。",
                                "confidence": 0.96,
                                "evidence_text": "以后回答先给结论",
                                "sensitivity": "safe",
                            }],
                        }, ensure_ascii=False)
                    }
                }]
            },
        )

    provider = OpenAICompatibleExtractionProvider(
        _config(),
        transport=httpx.MockTransport(handler),
    )

    candidates = await provider.extract(_turn())

    assert captured["model"] == "memory-extractor"
    assert captured["response_format"] == {"type": "json_object"}
    request_text = str(captured["messages"])
    assert "以后回答先给结论" in request_text
    assert "session-1" not in request_text
    assert candidates[0].kind is MemoryKind.PREFERENCE
    assert candidates[0].scope is MemoryScope.USER
    assert candidates[0].sensitivity is SensitivityDecision.SAFE


@pytest.mark.asyncio
async def test_provider_rejects_secret_before_network_call():
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    provider = OpenAICompatibleExtractionProvider(
        _config(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ExtractionInputRejectedError, match="sensitive"):
        await provider.extract(_turn(user_text="api_key=top-secret-value"))
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps({"schema_version": 2, "candidates": []}),
        json.dumps({"schema_version": 1, "candidates": [{"kind": "unknown"}]}),
    ],
)
async def test_provider_rejects_invalid_structured_output_without_leaking_body(content):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = OpenAICompatibleExtractionProvider(
        _config(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ExtractionResponseError) as caught:
        await provider.extract(_turn())
    assert content not in str(caught.value)
