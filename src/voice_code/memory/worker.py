from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime

from voice_code.audit import record_audit_event
from voice_code.memory.candidate_service import CandidateDispositionService
from voice_code.memory.candidates import (
    MemoryCandidate,
    MutationAction,
)
from voice_code.memory.extraction import (
    ExtractionInputRejectedError,
    ExtractionResponseError,
    ExtractionUnavailableError,
    MemoryExtractionProvider,
    TurnExtractionInput,
)
from voice_code.memory.policy import RelatedMemory, SemanticRelation
from voice_code.memory.repository import MemoryRepository

logger = logging.getLogger(__name__)


class MemoryExtractionWorker:
    def __init__(
        self,
        repository: MemoryRepository,
        provider: MemoryExtractionProvider,
        *,
        extractor_model: str = "memory-extractor",
        prompt_version: str = "memory-extraction-v1",
        min_confidence: float = 0.9,
        max_attempts: int = 5,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.extractor_model = extractor_model
        self.prompt_version = prompt_version
        self.max_attempts = max(max_attempts, 1)
        self.disposition = CandidateDispositionService(
            repository,
            min_confidence=min_confidence,
        )

    async def run_once(self, *, limit: int = 20) -> int:
        jobs = self.repository.claim_extraction_jobs(limit=limit, lease_seconds=30)
        completed = 0
        for job in jobs:
            started = time.perf_counter()
            run_id = f"run_{uuid.uuid4().hex}"
            try:
                extracted = await self.provider.extract(
                    TurnExtractionInput(
                        user_text=job.user_input,
                        assistant_text=job.assistant_response,
                        source_session_id=job.source_session_id,
                        source_turn_id=job.source_turn_id,
                        project_available=job.project_key is not None,
                    )
                )
                accepted = 0
                for item in extracted:
                    project_key = job.project_key if item.scope.value == "project" else None
                    candidate = MemoryCandidate(
                        candidate_id=f"cand_{uuid.uuid4().hex}",
                        user_id=job.user_id,
                        project_key=project_key,
                        scope=item.scope,
                        kind=item.kind,
                        content=item.content,
                        confidence=item.confidence,
                        evidence_text=item.evidence_text,
                        source_session_id=job.source_session_id,
                        source_turn_id=job.source_turn_id,
                        extraction_run_id=run_id,
                        extractor_model=self.extractor_model,
                        sensitivity=item.sensitivity,
                        created_at=datetime.now(UTC),
                    )
                    exact = self.repository.find_exact_active(
                        user_id=job.user_id,
                        project_key=project_key,
                        scope=item.scope,
                        content=item.content,
                    )
                    related = (
                        [RelatedMemory(exact, SemanticRelation.DUPLICATE, 1.0)]
                        if exact is not None
                        else []
                    )
                    result = self.disposition.process(
                        candidate,
                        mode=job.mode,
                        user_text=job.user_input,
                        related=related,
                    )
                    if result.decision.action in {
                        MutationAction.CREATE,
                        MutationAction.MERGE,
                        MutationAction.SUPERSEDE,
                    }:
                        accepted += 1
                self.repository.record_extraction_run(
                    run_id=run_id,
                    job=job,
                    extractor_model=self.extractor_model,
                    prompt_version=self.prompt_version,
                    candidate_count=len(extracted),
                    accepted_count=accepted,
                    status="completed",
                    latency_ms=_elapsed_ms(started),
                )
                self.repository.complete_extraction_job(job.job_id)
                record_audit_event(
                    event_type="memory.extraction",
                    actor="agent",
                    resource_id=f"memory-extraction:{run_id}",
                    outcome="completed",
                    rule=self.prompt_version,
                    approval_result=job.mode.value,
                )
                completed += 1
                logger.info(
                    "memory.extraction.completed",
                    extra={
                        "mode": job.mode.value,
                        "candidate_count": len(extracted),
                        "accepted_count": accepted,
                    },
                )
            except ExtractionInputRejectedError as exc:
                self._record_failure(job, run_id, started, exc, terminal=True)
            except ExtractionResponseError as exc:
                self._record_failure(job, run_id, started, exc, terminal=True)
            except ExtractionUnavailableError as exc:
                self._record_failure(job, run_id, started, exc, terminal=False)
            except Exception as exc:  # worker boundary: preserve the durable job
                self._record_failure(job, run_id, started, exc, terminal=False)
        return completed

    def _record_failure(self, job, run_id, started, error: Exception, *, terminal: bool) -> None:
        error_code = type(error).__name__
        self.repository.record_extraction_run(
            run_id=run_id,
            job=job,
            extractor_model=self.extractor_model,
            prompt_version=self.prompt_version,
            candidate_count=0,
            accepted_count=0,
            status="failed",
            latency_ms=_elapsed_ms(started),
            error_code=error_code,
        )
        should_dead_letter = terminal or job.attempts >= self.max_attempts
        if should_dead_letter:
            self.repository.dead_letter_extraction_job(
                job.job_id,
                error_code=error_code,
                attempts=job.attempts,
            )
        else:
            delay = min(300, 2 ** min(job.attempts, 8))
            self.repository.fail_extraction_job(
                job.job_id,
                error_code=error_code,
                delay_seconds=delay,
                terminal=False,
            )
        logger.warning(
            "memory.extraction.failed",
            extra={
                "mode": job.mode.value,
                "error_code": error_code,
                "terminal": terminal,
                "attempts": job.attempts,
            },
        )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1_000, 3)
