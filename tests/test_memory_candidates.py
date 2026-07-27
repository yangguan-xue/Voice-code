from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from voice_code.memory.candidates import (
    ExtractionMode,
    MemoryAuthority,
    MemoryCandidate,
    MemoryReviewState,
    MutationAction,
    SensitivityDecision,
)
from voice_code.memory.rag_models import MemoryKind, MemoryScope


def _candidate(**overrides) -> MemoryCandidate:
    values = {
        "candidate_id": "cand_0123456789abcdef",
        "user_id": "local",
        "project_key": None,
        "scope": MemoryScope.USER,
        "kind": MemoryKind.PREFERENCE,
        "content": "回答时先给结论。",
        "confidence": 0.96,
        "evidence_text": "以后回答先说结论",
        "source_session_id": "session-1",
        "source_turn_id": "turn-1",
        "extraction_run_id": "run_0123456789abcdef",
        "extractor_model": "extractor-model",
        "sensitivity": SensitivityDecision.SAFE,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return MemoryCandidate(**values)


def test_candidate_normalizes_content_and_is_immutable():
    candidate = _candidate(content="  回答时先给结论。  ")

    assert candidate.content == "回答时先给结论。"
    with pytest.raises(FrozenInstanceError):
        candidate.content = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), float("inf")])
def test_candidate_rejects_invalid_confidence(confidence):
    with pytest.raises(ValueError, match="confidence"):
        _candidate(confidence=confidence)


@pytest.mark.parametrize("content", ["", "abc", "x" * 1001])
def test_candidate_rejects_invalid_content(content):
    with pytest.raises(ValueError, match="content"):
        _candidate(content=content)


def test_project_candidate_requires_project_key():
    with pytest.raises(ValueError, match="project_key"):
        _candidate(scope=MemoryScope.PROJECT, project_key=None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_id", "bad id"),
        ("extraction_run_id", "run/unsafe"),
        ("source_session_id", ""),
        ("source_turn_id", ""),
        ("extractor_model", ""),
    ],
)
def test_candidate_rejects_invalid_identifiers(field, value):
    with pytest.raises(ValueError):
        _candidate(**{field: value})


def test_lifecycle_enums_have_stable_wire_values():
    assert MemoryAuthority.EXPLICIT.value == "explicit"
    assert MemoryReviewState.CONFLICT_PENDING.value == "conflict_pending"
    assert MutationAction.REQUIRE_CONFIRMATION.value == "require_confirmation"
    assert ExtractionMode.OFF.value == "off"
    assert SensitivityDecision.NEEDS_CONFIRMATION.value == "needs_confirmation"
    assert MemoryKind.USER_FACT.value == "user_fact"
