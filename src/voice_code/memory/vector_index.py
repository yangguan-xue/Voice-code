from __future__ import annotations

from typing import Protocol

from voice_code.memory.rag_models import MemoryRecord, VectorHit


class MemoryIndexUnavailableError(RuntimeError):
    pass


class MemoryVectorIndex(Protocol):
    async def ensure_collection(self) -> None: ...

    async def clear(self) -> None: ...

    async def upsert(self, record: MemoryRecord, vector: list[float]) -> None: ...

    async def delete(self, memory_id: str) -> None: ...

    async def hybrid_search(
        self,
        *,
        query: str,
        vector: list[float],
        user_id: str,
        project_key: str | None,
        limit: int,
    ) -> list[VectorHit]: ...

    async def health(self) -> bool: ...
