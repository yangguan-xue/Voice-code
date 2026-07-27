from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from voice_code.memory.candidates import ExtractionMode
from voice_code.memory.paths import get_memory_root


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _validate_service_url(value: str, *, field_name: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"Invalid {field_name}")
    is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
        raise ValueError(f"{field_name} must use HTTPS except for localhost")
    return normalized


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    dimension: int = 1024
    timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "base_url",
            _validate_service_url(self.base_url, field_name="embedding base URL"),
        )
        if not self.api_key.strip():
            raise ValueError("Embedding API key is required")
        if not self.model.strip() or len(self.model) > 128:
            raise ValueError("Invalid embedding model")
        if not 1 <= self.dimension <= 65_536:
            raise ValueError("Invalid embedding dimension")
        if not 0.1 <= self.timeout_seconds <= 60:
            raise ValueError("Invalid embedding timeout")


@dataclass(frozen=True, slots=True)
class MilvusConfig:
    uri: str
    token: str = field(default="", repr=False)
    collection_name: str = "reasoning_user_memory_v1"
    timeout_seconds: float = 0.5

    def __post_init__(self) -> None:
        object.__setattr__(self, "uri", _validate_service_url(self.uri, field_name="Milvus URI"))
        if not self.collection_name.replace("_", "").isalnum():
            raise ValueError("Invalid Milvus collection name")
        if not 0.1 <= self.timeout_seconds <= 30:
            raise ValueError("Invalid Milvus timeout")


@dataclass(frozen=True, slots=True)
class ExtractionConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    mode: ExtractionMode = ExtractionMode.OFF
    timeout_seconds: float = 10.0
    min_confidence: float = 0.9
    max_candidates_per_turn: int = 2
    prompt_version: str = "memory-extraction-v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "base_url",
            _validate_service_url(self.base_url, field_name="extraction base URL"),
        )
        if not self.api_key.strip():
            raise ValueError("Extraction API key is required")
        if not self.model.strip() or len(self.model) > 128:
            raise ValueError("Invalid extraction model")
        if not 0.1 <= self.timeout_seconds <= 60:
            raise ValueError("Invalid extraction timeout")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("Invalid extraction confidence")
        if not 1 <= self.max_candidates_per_turn <= 5:
            raise ValueError("Invalid extraction candidate limit")
        if not self.prompt_version.strip() or len(self.prompt_version) > 64:
            raise ValueError("Invalid extraction prompt version")


@dataclass(frozen=True, slots=True)
class MemoryRagConfig:
    database_path: Path
    enabled: bool = True
    retrieval_limit: int = 5
    prompt_char_budget: int = 4_000
    embedding: EmbeddingConfig | None = None
    milvus: MilvusConfig | None = None
    extraction_mode: ExtractionMode = ExtractionMode.OFF
    extraction: ExtractionConfig | None = None

    @classmethod
    def from_env(cls) -> MemoryRagConfig:
        embedding = _embedding_from_env()
        milvus = _milvus_from_env()
        extraction_mode, extraction = _extraction_from_env()
        return cls(
            database_path=get_memory_root() / "memory-v2.db",
            enabled=_env_bool("REASONING_MEMORY_ENABLED", True),
            retrieval_limit=int(os.getenv("REASONING_MEMORY_RETRIEVAL_LIMIT", "5")),
            prompt_char_budget=int(os.getenv("REASONING_MEMORY_PROMPT_CHAR_BUDGET", "4000")),
            embedding=embedding,
            milvus=milvus,
            extraction_mode=extraction_mode,
            extraction=extraction,
        )


def _embedding_from_env() -> EmbeddingConfig | None:
    base_url = os.getenv("MEMORY_EMBEDDING_BASE_URL", "").strip()
    api_key = os.getenv("MEMORY_EMBEDDING_API_KEY", "").strip()
    if not base_url and not api_key:
        return None
    if not base_url or not api_key:
        raise ValueError("Both embedding base URL and API key are required")
    return EmbeddingConfig(
        base_url=base_url,
        api_key=api_key,
        model=os.getenv("MEMORY_EMBEDDING_MODEL", "text-embedding-v4"),
        dimension=int(os.getenv("MEMORY_EMBEDDING_DIMENSION", "1024")),
        timeout_seconds=float(os.getenv("MEMORY_EMBEDDING_TIMEOUT_SECONDS", "2.0")),
    )


def _milvus_from_env() -> MilvusConfig | None:
    uri = os.getenv("MILVUS_URI", "").strip()
    if not uri:
        return None
    return MilvusConfig(
        uri=uri,
        token=os.getenv("MILVUS_TOKEN", ""),
        collection_name=os.getenv("MILVUS_COLLECTION", "reasoning_user_memory_v1"),
        timeout_seconds=float(os.getenv("MILVUS_TIMEOUT_SECONDS", "0.5")),
    )


def _extraction_from_env() -> tuple[ExtractionMode, ExtractionConfig | None]:
    mode = ExtractionMode(os.getenv("REASONING_MEMORY_EXTRACTION_MODE", "off").strip().lower())
    if mode is ExtractionMode.OFF:
        return mode, None
    base_url = os.getenv("MEMORY_EXTRACTION_BASE_URL", "").strip()
    api_key = os.getenv("MEMORY_EXTRACTION_API_KEY", "").strip()
    if not base_url or not api_key:
        raise ValueError("Extraction base URL and API key are required when extraction is enabled")
    model = os.getenv("MEMORY_EXTRACTION_MODEL", "").strip()
    if not model:
        raise ValueError("Extraction model is required when extraction is enabled")
    if mode is ExtractionMode.AUTOMATIC:
        gate_report = os.getenv("MEMORY_EXTRACTION_GATE_REPORT", "").strip()
        if not gate_report:
            raise ValueError("Automatic extraction requires a passing gate report")
        from voice_code.memory.evaluation import validate_gate_report

        validate_gate_report(Path(gate_report))
    return mode, ExtractionConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        mode=mode,
        timeout_seconds=float(os.getenv("MEMORY_EXTRACTION_TIMEOUT_SECONDS", "10")),
        min_confidence=float(os.getenv("MEMORY_EXTRACTION_MIN_CONFIDENCE", "0.9")),
        max_candidates_per_turn=int(
            os.getenv("MEMORY_EXTRACTION_MAX_CANDIDATES_PER_TURN", "2")
        ),
        prompt_version=os.getenv("MEMORY_EXTRACTION_PROMPT_VERSION", "memory-extraction-v1"),
    )
