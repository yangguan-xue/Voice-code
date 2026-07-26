from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from voice_code.memory.config import MilvusConfig
from voice_code.memory.rag_models import MemoryRecord, VectorHit
from voice_code.memory.vector_index import MemoryIndexUnavailableError

logger = logging.getLogger(__name__)
_INDEX_SCHEMA_VERSION = 1


class MilvusMemoryIndex:
    def __init__(
        self,
        config: MilvusConfig,
        *,
        dimension: int,
        embedding_model: str,
        client: Any | None = None,
    ) -> None:
        if dimension <= 0:
            raise ValueError("Milvus vector dimension must be positive")
        self.config = config
        self.dimension = dimension
        self.embedding_model = embedding_model
        if client is None:
            try:
                from pymilvus import MilvusClient
            except ImportError as exc:  # pragma: no cover - depends on optional install
                raise RuntimeError(
                    "Milvus support requires: uv sync --extra milvus"
                ) from exc
            kwargs: dict[str, str] = {"uri": config.uri}
            if config.token:
                kwargs["token"] = config.token
            client = MilvusClient(**kwargs)
        self._client = client

    async def ensure_collection(self) -> None:
        await asyncio.to_thread(self._ensure_collection_sync)

    async def clear(self) -> None:
        try:
            if self._client.has_collection(collection_name=self.config.collection_name):
                await asyncio.to_thread(
                    self._client.drop_collection,
                    collection_name=self.config.collection_name,
                )
            await self.ensure_collection()
        except Exception as exc:
            raise MemoryIndexUnavailableError("Milvus clear failed") from exc

    def _ensure_collection_sync(self) -> None:
        try:
            if self._client.has_collection(collection_name=self.config.collection_name):
                return
            from pymilvus import DataType, Function, FunctionType, MilvusClient

            schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field(
                field_name="memory_id",
                datatype=DataType.VARCHAR,
                is_primary=True,
                max_length=64,
            )
            schema.add_field(field_name="user_id", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="project_key", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="scope", datatype=DataType.VARCHAR, max_length=16)
            schema.add_field(field_name="kind", datatype=DataType.VARCHAR, max_length=32)
            schema.add_field(field_name="status", datatype=DataType.VARCHAR, max_length=16)
            schema.add_field(
                field_name="search_text",
                datatype=DataType.VARCHAR,
                max_length=8192,
                enable_analyzer=True,
                analyzer_params={"type": "chinese"},
            )
            schema.add_field(
                field_name="dense_vector",
                datatype=DataType.FLOAT_VECTOR,
                dim=self.dimension,
            )
            schema.add_field(
                field_name="sparse_vector",
                datatype=DataType.SPARSE_FLOAT_VECTOR,
            )
            schema.add_field(field_name="memory_version", datatype=DataType.INT64)
            schema.add_field(
                field_name="embedding_model",
                datatype=DataType.VARCHAR,
                max_length=128,
            )
            schema.add_field(field_name="schema_version", datatype=DataType.INT64)
            schema.add_function(
                Function(
                    name="memory_bm25",
                    function_type=FunctionType.BM25,
                    input_field_names=["search_text"],
                    output_field_names=["sparse_vector"],
                )
            )
            indexes = MilvusClient.prepare_index_params()
            indexes.add_index(
                field_name="dense_vector",
                index_type="AUTOINDEX",
                metric_type="COSINE",
            )
            indexes.add_index(
                field_name="sparse_vector",
                index_type="AUTOINDEX",
                metric_type="BM25",
            )
            self._client.create_collection(
                collection_name=self.config.collection_name,
                schema=schema,
                index_params=indexes,
            )
        except Exception as exc:
            raise MemoryIndexUnavailableError("Unable to initialize Milvus collection") from exc

    async def upsert(self, record: MemoryRecord, vector: list[float]) -> None:
        if len(vector) != self.dimension:
            raise ValueError("Milvus vector dimension does not match collection")
        payload = {
            "memory_id": record.id,
            "user_id": record.user_id,
            "project_key": record.project_key or "",
            "scope": record.scope.value,
            "kind": record.kind.value,
            "status": record.status.value,
            "search_text": record.content,
            "dense_vector": vector,
            "memory_version": record.version,
            "embedding_model": self.embedding_model,
            "schema_version": _INDEX_SCHEMA_VERSION,
        }
        try:
            await asyncio.to_thread(
                self._client.upsert,
                collection_name=self.config.collection_name,
                data=[payload],
            )
        except Exception as exc:
            raise MemoryIndexUnavailableError("Milvus upsert failed") from exc

    async def delete(self, memory_id: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.delete,
                collection_name=self.config.collection_name,
                ids=[memory_id],
            )
        except Exception as exc:
            raise MemoryIndexUnavailableError("Milvus delete failed") from exc

    async def hybrid_search(
        self,
        *,
        query: str,
        vector: list[float],
        user_id: str,
        project_key: str | None,
        limit: int,
    ) -> list[VectorHit]:
        if len(vector) != self.dimension:
            raise ValueError("Milvus query vector dimension does not match collection")
        try:
            from pymilvus import AnnSearchRequest, RRFRanker

            expression = _identity_filter(
                user_id=user_id,
                project_key=project_key,
                embedding_model=self.embedding_model,
            )
            reqs = [
                AnnSearchRequest(
                    data=[vector],
                    anns_field="dense_vector",
                    param={"metric_type": "COSINE", "params": {}},
                    limit=limit,
                    expr=expression,
                ),
                AnnSearchRequest(
                    data=[query],
                    anns_field="sparse_vector",
                    param={"metric_type": "BM25", "params": {}},
                    limit=limit,
                    expr=expression,
                ),
            ]
            response = await asyncio.to_thread(
                self._client.hybrid_search,
                collection_name=self.config.collection_name,
                reqs=reqs,
                ranker=RRFRanker(),
                limit=limit,
                output_fields=["memory_id", "memory_version"],
                timeout=self.config.timeout_seconds,
            )
        except Exception as exc:
            raise MemoryIndexUnavailableError("Milvus hybrid search failed") from exc

        if not isinstance(response, list) or not response:
            return []
        hits: list[VectorHit] = []
        for item in response[0]:
            if not isinstance(item, dict):
                continue
            entity = item.get("entity") if isinstance(item.get("entity"), dict) else {}
            memory_id = item.get("id") or entity.get("memory_id")
            version = entity.get("memory_version")
            score = item.get("distance", item.get("score", 0.0))
            if isinstance(memory_id, str) and isinstance(version, int) and isinstance(
                score, (int, float)
            ):
                hits.append(VectorHit(memory_id, version, float(score)))
        return hits

    async def health(self) -> bool:
        try:
            return bool(
                await asyncio.to_thread(
                    self._client.has_collection,
                    collection_name=self.config.collection_name,
                    timeout=self.config.timeout_seconds,
                )
            )
        except Exception:
            logger.warning("memory.index.health_failed", extra={"backend": "milvus"})
            return False


def _identity_filter(
    *,
    user_id: str,
    project_key: str | None,
    embedding_model: str,
) -> str:
    user_literal = json.dumps(user_id, ensure_ascii=False)
    model_literal = json.dumps(embedding_model, ensure_ascii=False)
    if project_key:
        project_literal = json.dumps(project_key, ensure_ascii=False)
        scope_filter = (
            f'(scope == "user" or (scope == "project" and project_key == {project_literal}))'
        )
    else:
        scope_filter = 'scope == "user"'
    return (
        f"user_id == {user_literal} and status == \"active\" "
        f"and embedding_model == {model_literal} and schema_version == {_INDEX_SCHEMA_VERSION} "
        f"and {scope_filter}"
    )
