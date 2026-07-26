from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from voice_code.memory.candidates import (
    MemoryAuthority,
    MemoryCandidate,
    MemoryMutationDecision,
    MutationAction,
    SensitivityDecision,
)
from voice_code.memory.rag_models import MemoryKind, MemoryRecord
from voice_code.security.redaction import redact_secrets


class SemanticRelation(StrEnum):
    DUPLICATE = "duplicate"
    COMPATIBLE = "compatible"
    CONTRADICTS = "contradicts"
    RELATED = "related"


@dataclass(frozen=True, slots=True)
class RelatedMemory:
    memory: MemoryRecord
    relation: SemanticRelation
    similarity: float


_AUTHORITY_RANK = {
    MemoryAuthority.AUTOMATIC_EXTRACTION: 1,
    MemoryAuthority.APPROVED_EXTRACTION: 2,
    MemoryAuthority.MIGRATED: 2,
    MemoryAuthority.EXPLICIT: 3,
}
_REPLACEMENT_CUES = ("改成", "替换之前", "取代之前", "不再", "以后不要")
_POLICY_OVERRIDE_CUES = (
    "绕过权限",
    "忽略权限",
    "关闭安全",
    "覆盖系统提示",
    "忽略系统提示",
)


def decide_candidate(
    candidate: MemoryCandidate,
    *,
    related: Sequence[RelatedMemory],
    authority: MemoryAuthority,
    user_text: str,
    min_confidence: float = 0.9,
) -> MemoryMutationDecision:
    rejection = _rejection_reason(candidate, user_text, min_confidence)
    if rejection:
        return _decision(candidate, MutationAction.REJECT, rejection)
    if candidate.sensitivity is SensitivityDecision.NEEDS_CONFIRMATION:
        return _decision(
            candidate,
            MutationAction.REQUIRE_CONFIRMATION,
            "SENSITIVITY_REVIEW_REQUIRED",
            requires_user_action=True,
        )
    if authority is not MemoryAuthority.EXPLICIT and candidate.kind is MemoryKind.PROJECT_FACT:
        return _decision(
            candidate,
            MutationAction.REQUIRE_CONFIRMATION,
            "PROJECT_FACT_REVIEW_REQUIRED",
            requires_user_action=True,
        )

    ordered = sorted(related, key=lambda item: item.similarity, reverse=True)
    duplicate = next(
        (item for item in ordered if item.relation is SemanticRelation.DUPLICATE),
        None,
    )
    if duplicate:
        return _decision(
            candidate,
            MutationAction.IGNORE_DUPLICATE,
            "EXACT_OR_SEMANTIC_DUPLICATE",
            target=duplicate,
        )

    contradiction = next(
        (item for item in ordered if item.relation is SemanticRelation.CONTRADICTS),
        None,
    )
    if contradiction:
        return _decide_contradiction(candidate, authority, user_text, contradiction)

    compatible = next(
        (item for item in ordered if item.relation is SemanticRelation.COMPATIBLE),
        None,
    )
    if compatible:
        if _AUTHORITY_RANK[authority] >= _AUTHORITY_RANK[compatible.memory.authority]:
            return _decision(
                candidate,
                MutationAction.MERGE,
                "COMPATIBLE_HIGH_SIMILARITY",
                target=compatible,
            )
        return _decision(
            candidate,
            MutationAction.IGNORE_DUPLICATE,
            "LOWER_AUTHORITY_COMPATIBLE",
            target=compatible,
        )

    return _decision(candidate, MutationAction.CREATE, "NEW_STABLE_MEMORY")


def _rejection_reason(
    candidate: MemoryCandidate,
    user_text: str,
    min_confidence: float,
) -> str | None:
    if candidate.sensitivity is SensitivityDecision.SENSITIVE:
        return "SENSITIVE"
    if redact_secrets(candidate.content) != candidate.content:
        return "SENSITIVE_CONTENT"
    if candidate.confidence < min_confidence:
        return "LOW_CONFIDENCE"
    normalized_user = " ".join(user_text.split()).casefold()
    if candidate.evidence_text.casefold() not in normalized_user:
        return "EVIDENCE_NOT_FOUND"
    combined = f"{candidate.content} {candidate.evidence_text}".casefold()
    if any(cue in combined for cue in _POLICY_OVERRIDE_CUES):
        return "POLICY_OVERRIDE"
    return None


def _decide_contradiction(
    candidate: MemoryCandidate,
    authority: MemoryAuthority,
    user_text: str,
    target: RelatedMemory,
) -> MemoryMutationDecision:
    existing_authority = target.memory.authority
    if authority is MemoryAuthority.EXPLICIT:
        if existing_authority is not MemoryAuthority.EXPLICIT or any(
            cue in user_text for cue in _REPLACEMENT_CUES
        ):
            return _decision(
                candidate,
                MutationAction.SUPERSEDE,
                "EXPLICIT_REPLACEMENT",
                target=target,
            )
        return _decision(
            candidate,
            MutationAction.CONFLICT,
            "AMBIGUOUS_EXPLICIT_CONFLICT",
            target=target,
            requires_user_action=True,
        )
    if existing_authority is MemoryAuthority.EXPLICIT:
        reason = "AUTOMATIC_CONFLICTS_WITH_EXPLICIT"
    else:
        reason = "AUTOMATIC_CONFLICT_REVIEW_REQUIRED"
    return _decision(
        candidate,
        MutationAction.REQUIRE_CONFIRMATION,
        reason,
        target=target,
        requires_user_action=True,
    )


def _decision(
    candidate: MemoryCandidate,
    action: MutationAction,
    reason: str,
    *,
    target: RelatedMemory | None = None,
    requires_user_action: bool = False,
) -> MemoryMutationDecision:
    return MemoryMutationDecision(
        action=action,
        candidate_id=candidate.candidate_id,
        target_memory_id=target.memory.id if target else None,
        reason_code=reason,
        similarity=target.similarity if target else None,
        requires_user_action=requires_user_action,
    )
