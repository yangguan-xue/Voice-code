from __future__ import annotations

import json

import httpx
import pytest

from voice_code.memory.config import EmbeddingConfig, MemoryRagConfig
from voice_code.memory.embedding import (
    EmbeddingResponseError,
    OpenAICompatibleEmbeddingProvider,
)


def test_memory_config_reads_embedding_secret_without_exposing_it(monkeypatch, tmp_path):
    monkeypatch.setenv("REASONING_HOME", str(tmp_path))
    monkeypatch.setenv("MEMORY_EMBEDDING_BASE_URL", "https://embedding.example/v1")
    monkeypatch.setenv("MEMORY_EMBEDDING_API_KEY", "secret-value")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv("MEMORY_EMBEDDING_DIMENSION", "1024")

    config = MemoryRagConfig.from_env()

    assert config.embedding is not None
    assert config.embedding.api_key == "secret-value"
    assert "secret-value" not in repr(config)
    assert config.database_path == tmp_path / "memory" / "memory-v2.db"


@pytest.mark.asyncio
async def test_embedding_provider_calls_openai_compatible_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://embedding.example/v1/embeddings"
        assert request.headers["authorization"] == "Bearer test-secret"
        payload = json.loads(request.content)
        assert payload == {
            "model": "text-embedding-v4",
            "input": ["偏好简洁回答"],
            "dimensions": 3,
        }
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]},
        )

    provider = OpenAICompatibleEmbeddingProvider(
        EmbeddingConfig(
            base_url="https://embedding.example/v1",
            api_key="test-secret",
            model="text-embedding-v4",
            dimension=3,
        ),
        transport=httpx.MockTransport(handler),
    )

    assert await provider.embed_query("偏好简洁回答") == [0.1, 0.2, 0.3]
    await provider.aclose()


@pytest.mark.asyncio
async def test_embedding_provider_rejects_wrong_vector_dimension():
    provider = OpenAICompatibleEmbeddingProvider(
        EmbeddingConfig(
            base_url="https://embedding.example/v1",
            api_key="test-secret",
            model="text-embedding-v4",
            dimension=3,
        ),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
            )
        ),
    )

    with pytest.raises(EmbeddingResponseError, match="dimension"):
        await provider.embed_query("test")
    await provider.aclose()


def test_embedding_config_rejects_insecure_remote_url():
    with pytest.raises(ValueError, match="HTTPS"):
        EmbeddingConfig(
            base_url="http://embedding.example/v1",
            api_key="secret",
            model="text-embedding-v4",
            dimension=1024,
        )
