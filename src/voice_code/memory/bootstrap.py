from __future__ import annotations

import logging

from voice_code.memory.config import MemoryRagConfig
from voice_code.memory.embedding import OpenAICompatibleEmbeddingProvider
from voice_code.memory.extraction import OpenAICompatibleExtractionProvider
from voice_code.memory.milvus_index import MilvusMemoryIndex
from voice_code.memory.rag_service import MemoryRagService
from voice_code.memory.repository import MemoryRepository
from voice_code.memory.worker import MemoryExtractionWorker

logger = logging.getLogger(__name__)


def build_memory_rag_service(config: MemoryRagConfig | None = None) -> MemoryRagService | None:
    resolved = config or MemoryRagConfig.from_env()
    if not resolved.enabled:
        return None
    repository = MemoryRepository(resolved.database_path)
    embedder = None
    vector_index = None
    extraction_worker = None
    if resolved.embedding is not None and resolved.milvus is not None:
        embedder = OpenAICompatibleEmbeddingProvider(resolved.embedding)
        vector_index = MilvusMemoryIndex(
            resolved.milvus,
            dimension=resolved.embedding.dimension,
            embedding_model=resolved.embedding.model,
        )
    elif resolved.embedding is not None or resolved.milvus is not None:
        logger.warning(
            "memory.config.partial",
            extra={"backend": "sqlite_fts", "error_code": "INCOMPLETE_VECTOR_CONFIG"},
        )
    if resolved.extraction is not None:
        extraction_provider = OpenAICompatibleExtractionProvider(resolved.extraction)
        extraction_worker = MemoryExtractionWorker(
            repository,
            extraction_provider,
            extractor_model=resolved.extraction.model,
            prompt_version=resolved.extraction.prompt_version,
            min_confidence=resolved.extraction.min_confidence,
        )
    return MemoryRagService(
        repository,
        embedder=embedder,
        vector_index=vector_index,
        prompt_char_budget=resolved.prompt_char_budget,
        retrieval_limit=resolved.retrieval_limit,
        extraction_mode=resolved.extraction_mode,
        extraction_worker=extraction_worker,
    )
