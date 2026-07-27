from __future__ import annotations

import json

import pytest

from voice_code.memory.config import MilvusConfig
from voice_code.memory.milvus_index import MilvusMemoryIndex
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.repository import MemoryRepository


class FakeMilvusClient:
    def __init__(self) -> None:
        self.upserted: list[dict] = []
        self.deleted: list[str] = []
        self.search_calls: list[dict] = []
        self.collection_exists = True
        self.created_collection: dict | None = None

    def upsert(self, **kwargs):
        self.upserted.extend(kwargs["data"])

    def delete(self, **kwargs):
        self.deleted.extend(kwargs["ids"])

    def hybrid_search(self, **kwargs):
        self.search_calls.append(kwargs)
        return [[{
            "id": "mem_1",
            "distance": 0.91,
            "entity": {"memory_version": 2},
        }]]

    def has_collection(self, **_kwargs):
        return self.collection_exists

    def create_collection(self, **kwargs):
        self.created_collection = kwargs
        self.collection_exists = True


@pytest.mark.asyncio
async def test_milvus_index_creates_dense_and_chinese_bm25_schema():
    client = FakeMilvusClient()
    client.collection_exists = False
    index = MilvusMemoryIndex(
        MilvusConfig(uri="http://localhost:19530"),
        dimension=1024,
        embedding_model="text-embedding-v4",
        client=client,
    )

    await index.ensure_collection()

    assert client.created_collection is not None
    schema = client.created_collection["schema"]
    fields = {field.name: field for field in schema.fields}
    assert "dense_vector" in fields
    assert "sparse_vector" in fields
    assert fields["dense_vector"].params["dim"] == 1024
    assert "embedding_model" in fields
    assert "schema_version" in fields
    assert json.loads(fields["search_text"].params["analyzer_params"]) == {"type": "chinese"}


@pytest.mark.asyncio
async def test_milvus_index_upserts_and_deletes_by_memory_id(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    record = repository.remember(
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content="偏好简洁回答。",
        source_session_id="session-1",
    ).entry
    client = FakeMilvusClient()
    index = MilvusMemoryIndex(
        MilvusConfig(uri="http://localhost:19530"),
        dimension=3,
        embedding_model="text-embedding-v4",
        client=client,
    )

    await index.upsert(record, [0.1, 0.2, 0.3])
    await index.delete(record.id)

    assert client.upserted[0]["memory_id"] == record.id
    assert client.upserted[0]["dense_vector"] == [0.1, 0.2, 0.3]
    assert client.upserted[0]["embedding_model"] == "text-embedding-v4"
    assert client.upserted[0]["schema_version"] == 1
    assert client.deleted == [record.id]


@pytest.mark.asyncio
async def test_milvus_hybrid_search_uses_dense_bm25_and_identity_filter():
    client = FakeMilvusClient()
    index = MilvusMemoryIndex(
        MilvusConfig(uri="http://localhost:19530"),
        dimension=3,
        embedding_model="text-embedding-v4",
        client=client,
    )

    hits = await index.hybrid_search(
        query="回答风格",
        vector=[0.1, 0.2, 0.3],
        user_id="local",
        project_key="project-a",
        limit=5,
    )

    call = client.search_calls[0]
    assert len(call["reqs"]) == 2
    assert call["reqs"][0].anns_field == "dense_vector"
    assert call["reqs"][1].anns_field == "sparse_vector"
    assert 'user_id == "local"' in call["reqs"][0].expr
    assert 'project_key == "project-a"' in call["reqs"][0].expr
    assert 'embedding_model == "text-embedding-v4"' in call["reqs"][0].expr
    assert "schema_version == 1" in call["reqs"][0].expr
    assert hits[0].memory_id == "mem_1"
    assert hits[0].memory_version == 2
