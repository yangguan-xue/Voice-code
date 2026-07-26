from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from voice_code.memory.candidates import MemoryAuthority
from voice_code.memory.repository import MemoryRepository


@dataclass(frozen=True, slots=True)
class ConsolidationProposal:
    keeper_id: str
    duplicate_id: str
    similarity: float
    reason_code: str = "COMPATIBLE_AUTOMATIC_DUPLICATE"


class MemoryConsolidator:
    def __init__(
        self,
        repository: MemoryRepository,
        *,
        similarity_threshold: float = 0.9,
    ) -> None:
        if not 0.8 <= similarity_threshold <= 1:
            raise ValueError("Consolidation threshold must be between 0.8 and 1")
        self.repository = repository
        self.similarity_threshold = similarity_threshold

    def plan(self, *, user_id: str) -> list[ConsolidationProposal]:
        memories = [
            memory
            for memory in self.repository.list(user_id=user_id, limit=500)
            if memory.authority is MemoryAuthority.AUTOMATIC_EXTRACTION
        ]
        proposals: list[ConsolidationProposal] = []
        consumed: set[str] = set()
        for index, keeper in enumerate(memories):
            if keeper.id in consumed:
                continue
            for duplicate in memories[index + 1 :]:
                if duplicate.id in consumed or not _same_partition(keeper, duplicate):
                    continue
                similarity = SequenceMatcher(
                    None,
                    _normalized(keeper.content),
                    _normalized(duplicate.content),
                ).ratio()
                if similarity >= self.similarity_threshold:
                    proposals.append(
                        ConsolidationProposal(keeper.id, duplicate.id, similarity)
                    )
                    consumed.add(duplicate.id)
        return proposals

    def apply(
        self,
        proposals: list[ConsolidationProposal],
        *,
        user_id: str,
    ) -> int:
        return sum(
            self.repository.consolidate_automatic_pair(
                proposal.keeper_id,
                proposal.duplicate_id,
                user_id=user_id,
                reason_code=proposal.reason_code,
            )
            for proposal in proposals
        )


def _same_partition(first, second) -> bool:
    return (
        first.user_id == second.user_id
        and first.project_key == second.project_key
        and first.scope is second.scope
        and first.kind is second.kind
    )


def _normalized(content: str) -> str:
    return "".join(character for character in content.casefold() if character.isalnum())
