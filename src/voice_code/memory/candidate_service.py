from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from voice_code.memory.candidates import (
    ExtractionMode,
    MemoryAuthority,
    MemoryCandidate,
    MemoryMutationDecision,
    MutationAction,
)
from voice_code.memory.policy import RelatedMemory, decide_candidate
from voice_code.memory.rag_models import MemoryRecord
from voice_code.memory.repository import MemoryRepository


@dataclass(frozen=True, slots=True)
class CandidateDispositionResult:
    decision: MemoryMutationDecision
    memory: MemoryRecord | None
    applied: bool


class CandidateDispositionService:
    def __init__(self, repository: MemoryRepository, *, min_confidence: float = 0.9) -> None:
        self.repository = repository
        self.min_confidence = min_confidence

    def process(
        self,
        candidate: MemoryCandidate,
        *,
        mode: ExtractionMode,
        user_text: str,
        related: Sequence[RelatedMemory],
        authority: MemoryAuthority = MemoryAuthority.AUTOMATIC_EXTRACTION,
        actor_id: str = "memory-extractor",
    ) -> CandidateDispositionResult:
        if mode is ExtractionMode.OFF:
            raise ValueError("Cannot process a candidate while extraction mode is off")
        if mode is ExtractionMode.SHADOW:
            decision = MemoryMutationDecision(
                action=MutationAction.REJECT,
                candidate_id=candidate.candidate_id,
                target_memory_id=None,
                reason_code="SHADOW_ONLY",
            )
        elif mode is ExtractionMode.REVIEW:
            decision = MemoryMutationDecision(
                action=MutationAction.REQUIRE_CONFIRMATION,
                candidate_id=candidate.candidate_id,
                target_memory_id=None,
                reason_code="REVIEW_MODE",
                requires_user_action=True,
            )
        else:
            decision = decide_candidate(
                candidate,
                related=related,
                authority=authority,
                user_text=user_text,
                min_confidence=self.min_confidence,
            )
        memory, applied = self.repository.apply_candidate_decision(
            candidate,
            decision,
            authority=authority,
            actor_type="user" if authority is MemoryAuthority.EXPLICIT else "extractor",
            actor_id=actor_id,
        )
        return CandidateDispositionResult(decision=decision, memory=memory, applied=applied)
