from __future__ import annotations

import math
from typing import Any

import httpx

from voice_code.memory.config import EmbeddingConfig


class EmbeddingError(RuntimeError):
    """Base error with intentionally non-sensitive messages."""


class EmbeddingUnavailableError(EmbeddingError):
    pass


class EmbeddingResponseError(EmbeddingError):
    pass


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts or len(texts) > 64:
            raise ValueError("Embedding batch size must be between 1 and 64")
        if any(not text.strip() or len(text) > 32_000 for text in texts):
            raise ValueError("Embedding input is empty or too large")
        try:
            async with httpx.AsyncClient(
                base_url=f"{self.config.base_url}/",
                timeout=self.config.timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    "embeddings",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    json={
                        "model": self.config.model,
                        "input": texts,
                        "dimensions": self.config.dimension,
                    },
                )
                response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            raise EmbeddingUnavailableError("Embedding service request failed") from exc

        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise EmbeddingResponseError("Embedding service returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise EmbeddingResponseError("Embedding response is missing data")

        ordered: list[tuple[int, list[float]]] = []
        for position, item in enumerate(payload["data"]):
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise EmbeddingResponseError("Embedding response contains an invalid item")
            index = item.get("index", position)
            if not isinstance(index, int):
                raise EmbeddingResponseError("Embedding response contains an invalid index")
            vector = item["embedding"]
            if len(vector) != self.config.dimension:
                raise EmbeddingResponseError("Embedding vector dimension does not match config")
            if not all(
                isinstance(value, (int, float)) and math.isfinite(value) for value in vector
            ):
                raise EmbeddingResponseError("Embedding vector contains invalid values")
            ordered.append((index, [float(value) for value in vector]))
        ordered.sort(key=lambda pair: pair[0])
        if len(ordered) != len(texts) or [index for index, _ in ordered] != list(range(len(texts))):
            raise EmbeddingResponseError("Embedding response count does not match input")
        return [vector for _, vector in ordered]

    async def aclose(self) -> None:
        return None
