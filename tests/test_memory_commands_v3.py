from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from voice_code.commands import parse_command
from voice_code.memory import cli as memory_cli
from voice_code.memory.candidate_service import CandidateDispositionService
from voice_code.memory.candidates import (
    CandidateStatus,
    ExtractionMode,
    MemoryAuthority,
    MemoryCandidate,
    SensitivityDecision,
)
from voice_code.memory.policy import RelatedMemory, SemanticRelation
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.rag_service import MemoryRagService
from voice_code.memory.repository import MemoryRepository


def _candidate(candidate_id="cand_0123456789abcdef") -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        content="回答时先给结论。",
        confidence=0.96,
        evidence_text="以后回答先给结论",
        source_session_id="session-1",
        source_turn_id="turn-1",
        extraction_run_id="run_0123456789abcdef",
        extractor_model="extractor-model",
        sensitivity=SensitivityDecision.SAFE,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _pending_candidate(repository: MemoryRepository) -> MemoryCandidate:
    candidate = _candidate()
    CandidateDispositionService(repository).process(
        candidate,
        mode=ExtractionMode.REVIEW,
        user_text="以后回答先给结论",
        related=[],
    )
    return candidate


def test_candidate_can_be_listed_approved_and_explained(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    candidate = _pending_candidate(repository)
    service = MemoryRagService(repository)

    pending = service.list_candidates(user_id="local")
    approved = service.approve_candidate(candidate.candidate_id, user_id="local")
    explanation = service.explain_memory(approved.id, user_id="local")

    assert pending[0].status is CandidateStatus.CONFIRMATION_PENDING
    assert approved.authority is MemoryAuthority.APPROVED_EXTRACTION
    assert explanation["memory_id"] == approved.id
    assert explanation["evidence"][0]["source_turn_id"] == "turn-1"
    assert any(item["action"] == "create" for item in explanation["mutations"])


def test_candidate_approval_enforces_owner_and_is_idempotent(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    candidate = _pending_candidate(repository)
    service = MemoryRagService(repository)

    with pytest.raises(ValueError, match="not found"):
        service.approve_candidate(candidate.candidate_id, user_id="other-user")

    first = service.approve_candidate(candidate.candidate_id, user_id="local")
    second = service.approve_candidate(candidate.candidate_id, user_id="local")
    assert second.id == first.id
    assert len(service.list(user_id="local")) == 1


def test_candidate_can_be_rejected_without_creating_memory(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    candidate = _pending_candidate(repository)
    service = MemoryRagService(repository)

    assert service.reject_candidate(candidate.candidate_id, user_id="local") is True
    assert service.list(user_id="local") == []
    candidate_states = {
        item.candidate.candidate_id: item.status
        for item in service.list_candidates(user_id="local")
    }
    assert candidate_states[candidate.candidate_id] is CandidateStatus.REJECTED


def test_memory_edit_is_versioned_and_rejects_secrets(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    service = MemoryRagService(repository)
    memory = service.remember(
        "回答尽量简洁。",
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        session_id="session-1",
    ).entry

    edited = service.edit_memory(memory.id, "回答先给结论。", user_id="local")

    assert edited.version == 2
    assert edited.content == "回答先给结论。"
    with pytest.raises(ValueError, match="secret-like"):
        service.edit_memory(memory.id, "api_key=top-secret-value", user_id="local")


def test_memory_management_commands_parse_arguments():
    assert parse_command("/memory candidates").args["action"] == "candidates"
    assert parse_command("/memory approve cand_1").args["entry_id"] == "cand_1"
    assert parse_command("/memory reject cand_1").args["entry_id"] == "cand_1"
    assert parse_command("/memory why mem_1").args["entry_id"] == "mem_1"
    edit = parse_command('/memory edit mem_1 "新的长期偏好"')
    assert edit.args["text"] == "新的长期偏好"
    resolve = parse_command("/memory resolve cand_1 --keep mem_1")
    assert resolve.args["keep_id"] == "mem_1"


def test_dlq_commands_are_exposed_for_list_explain_and_replay(monkeypatch, capsys):
    calls = []

    class Repository:
        def list_dead_letters(self):
            calls.append(("list", None))
            return [{"dead_letter_id": "dlq_1", "source_type": "outbox"}]

        def explain_dead_letter(self, dead_letter_id):
            calls.append(("explain", dead_letter_id))
            return {"dead_letter_id": dead_letter_id, "error_code": "TimeoutError"}

        def replay_dead_letter(self, dead_letter_id):
            calls.append(("replay", dead_letter_id))
            return True

    class Service:
        repository = Repository()

    monkeypatch.setattr(memory_cli, "build_memory_rag_service", lambda: Service())

    memory_cli.main(["dlq-list"])
    assert json.loads(capsys.readouterr().out) == [
        {"dead_letter_id": "dlq_1", "source_type": "outbox"}
    ]
    memory_cli.main(["dlq-explain", "--dead-letter-id", "dlq_1"])
    assert json.loads(capsys.readouterr().out) == {
        "dead_letter_id": "dlq_1",
        "error_code": "TimeoutError",
    }
    memory_cli.main(["dlq-replay", "--dead-letter-id", "dlq_1"])
    assert json.loads(capsys.readouterr().out) == {"dead_letter_id": "dlq_1", "replayed": True}
    assert calls == [("list", None), ("explain", "dlq_1"), ("replay", "dlq_1")]



def test_explicit_conflict_can_keep_existing_memory(tmp_path):
    repository = MemoryRepository(tmp_path / "memory.db")
    service = MemoryRagService(repository)
    existing = service.remember(
        "回答尽量简洁。",
        user_id="local",
        project_key=None,
        scope=MemoryScope.USER,
        session_id="session-1",
    ).entry
    candidate = _candidate()
    result = CandidateDispositionService(repository).process(
        candidate,
        mode=ExtractionMode.AUTOMATIC,
        authority=MemoryAuthority.EXPLICIT,
        actor_id="local",
        user_text="以后回答先给结论",
        related=[RelatedMemory(existing, SemanticRelation.CONTRADICTS, 0.95)],
    )

    assert result.decision.action.value == "conflict"
    assert service.list(user_id="local") == []
    conflict_candidate = service.list_conflicts(user_id="local")[0].candidate
    assert conflict_candidate.candidate_id == candidate.candidate_id

    kept = service.resolve_conflict(
        candidate.candidate_id,
        keep_memory_id=existing.id,
        user_id="local",
    )

    assert kept.id == existing.id
    assert service.list(user_id="local")[0].id == existing.id
    conflict_states = {
        item.candidate.candidate_id: item.status
        for item in service.list_candidates(user_id="local")
    }
    assert conflict_states[candidate.candidate_id] is CandidateStatus.REJECTED
