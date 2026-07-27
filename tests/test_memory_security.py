from __future__ import annotations

import json

import httpx
import pytest

from voice_code.memory.candidates import ExtractionMode
from voice_code.memory.config import ExtractionConfig
from voice_code.memory.extraction import (
    ExtractionResponseError,
    OpenAICompatibleExtractionProvider,
    TurnExtractionInput,
)


@pytest.mark.asyncio
async def test_extractor_cannot_supply_trusted_identity_fields():
    candidate = {
        "kind": "preference",
        "scope": "user",
        "content": "回答时先给结论。",
        "confidence": 0.99,
        "evidence_text": "以后回答先给结论",
        "sensitivity": "safe",
        "user_id": "other-user",
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "schema_version": 1,
                            "candidates": [candidate],
                        })
                    }
                }]
            },
        )

    provider = OpenAICompatibleExtractionProvider(
        ExtractionConfig(
            base_url="https://extractor.example/v1",
            api_key="private-key",
            model="extractor-model",
            mode=ExtractionMode.SHADOW,
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ExtractionResponseError):
        await provider.extract(
            TurnExtractionInput(
                user_text="以后回答先给结论",
                assistant_text="明白。",
                source_session_id="session-1",
                source_turn_id="turn-1",
                project_available=False,
            )
        )
