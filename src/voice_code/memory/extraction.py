from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Protocol

import httpx

from voice_code.memory.candidates import SensitivityDecision
from voice_code.memory.config import ExtractionConfig
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.security.redaction import redact_secrets


class ExtractionError(RuntimeError):
    pass


class ExtractionUnavailableError(ExtractionError):
    pass


class ExtractionResponseError(ExtractionError):
    pass


class ExtractionInputRejectedError(ExtractionError):
    pass


@dataclass(frozen=True, slots=True)
class TurnExtractionInput:
    user_text: str
    assistant_text: str
    source_session_id: str
    source_turn_id: str
    project_available: bool

    def __post_init__(self) -> None:
        if not self.user_text.strip() or len(self.user_text) > 8_000:
            raise ValueError("Invalid extraction user text")
        if len(self.assistant_text) > 8_000:
            raise ValueError("Invalid extraction assistant text")
        for value in (self.source_session_id, self.source_turn_id):
            if not value.strip() or len(value) > 128:
                raise ValueError("Invalid extraction provenance")


@dataclass(frozen=True, slots=True)
class ExtractedCandidate:
    kind: MemoryKind
    scope: MemoryScope
    content: str
    confidence: float
    evidence_text: str
    sensitivity: SensitivityDecision

    def __post_init__(self) -> None:
        content = " ".join(self.content.split()).strip()
        evidence = " ".join(self.evidence_text.split()).strip()
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "evidence_text", evidence)
        if not 4 <= len(content) <= 1_000:
            raise ValueError("Invalid extracted candidate content")
        if not evidence or len(evidence) > 500:
            raise ValueError("Invalid extracted candidate evidence")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Invalid extracted candidate confidence")


class MemoryExtractionProvider(Protocol):
    async def extract(self, turn: TurnExtractionInput) -> list[ExtractedCandidate]: ...


class OpenAICompatibleExtractionProvider:
    def __init__(
        self,
        config: ExtractionConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    async def extract(self, turn: TurnExtractionInput) -> list[ExtractedCandidate]:
        if redact_secrets(turn.user_text) != turn.user_text or (
            redact_secrets(turn.assistant_text) != turn.assistant_text
        ):
            raise ExtractionInputRejectedError("Extraction input contains sensitive content")
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_text": turn.user_text[:4_000],
                            "assistant_text": turn.assistant_text[:4_000],
                            "project_available": turn.project_available,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        response = await self._post_with_one_retry(payload)
        candidates = self._parse_response(response)
        if not turn.project_available and any(
            candidate.scope is MemoryScope.PROJECT for candidate in candidates
        ):
            raise ExtractionResponseError(
                "Extraction provider returned project scope without project context"
            )
        return candidates

    async def _post_with_one_retry(self, payload: dict) -> httpx.Response:
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=self.config.timeout_seconds,
                    transport=self._transport,
                ) as client:
                    response = await client.post(
                        f"{self.config.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.config.api_key}"},
                        json=payload,
                    )
                if response.status_code < 500:
                    if response.is_error:
                        raise ExtractionUnavailableError("Extraction provider rejected request")
                    return response
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == 1:
                    raise ExtractionUnavailableError("Extraction provider unavailable") from exc
            if attempt == 1:
                raise ExtractionUnavailableError("Extraction provider unavailable")
        raise AssertionError("unreachable")

    def _parse_response(self, response: httpx.Response) -> list[ExtractedCandidate]:
        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
            document = json.loads(content)
            if document.get("schema_version") != 1:
                raise ValueError("schema")
            raw_candidates = document.get("candidates")
            if not isinstance(raw_candidates, list):
                raise ValueError("candidates")
            if len(raw_candidates) > self.config.max_candidates_per_turn:
                raise ValueError("candidate limit")
            return [_parse_candidate(item) for item in raw_candidates]
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ExtractionResponseError("Extraction provider returned invalid data") from exc


def _parse_candidate(value: object) -> ExtractedCandidate:
    if not isinstance(value, dict):
        raise ValueError("candidate")
    required = {
        "kind",
        "scope",
        "content",
        "confidence",
        "evidence_text",
        "sensitivity",
    }
    if set(value) != required:
        raise ValueError("candidate fields")
    return ExtractedCandidate(
        kind=MemoryKind(str(value["kind"])),
        scope=MemoryScope(str(value["scope"])),
        content=str(value["content"]),
        confidence=float(value["confidence"]),
        evidence_text=str(value["evidence_text"]),
        sensitivity=SensitivityDecision(str(value["sensitivity"])),
    )


_SYSTEM_PROMPT = """Extract only stable user preferences, habits, feedback, or durable facts.
Return strict JSON with schema_version=1 and candidates. Return an empty list when nothing is
stable. Evidence must be a short exact excerpt from user_text. Never extract secrets, transient
task details, tool output, permissions, or inferred claims. Use project scope only when
project_available is true."""
