"""Evidence-first decisions for the outer engineering loop."""

from __future__ import annotations

import hashlib

from voice_code.goals.types import (
    GoalAction,
    GoalDecision,
    GoalSpec,
    GoalState,
    VerificationEvidence,
)


def decide_next_action(
    spec: GoalSpec,
    state: GoalState,
    evidence: list[VerificationEvidence],
    *,
    reviewer_blockers: list[str] | None = None,
) -> GoalDecision:
    if state.stop_requested:
        return GoalDecision(GoalAction.STOP, "stop requested")
    failed = [item for item in evidence if not item.passed]
    blockers = reviewer_blockers or []

    # Deterministic gates green: reviewer can only block on real evidence gaps,
    # not on "you didn't paste the output again". When verify passed and we've
    # already iterated enough to let the builder address substance, let the
    # deterministic gates decide finish — reviewer blockers degrade to advisory.
    if not failed and state.iteration >= spec.min_review_iterations:
        return GoalDecision(
            GoalAction.FINISH,
            "deterministic gates passed; reviewer blockers are advisory-only after min iterations",
        )

    if not failed and not blockers:
        return GoalDecision(GoalAction.FINISH, "all deterministic gates and review passed")

    failure_text = "\n".join(
        f"$ {item.command}\n{item.stderr or item.stdout}" for item in failed
    )
    if blockers:
        failure_text += "\nReviewer blockers:\n- " + "\n- ".join(blockers)
    signature = hashlib.sha256(failure_text.encode("utf-8")).hexdigest()[:16]
    repeated = state.repeated_failure_count + 1 if signature == state.last_failure_signature else 1
    if repeated >= spec.max_consecutive_same_failure:
        return GoalDecision(
            GoalAction.ESCALATE,
            "same failure repeated without progress",
            failure_signature=signature,
        )
    if state.iteration >= spec.max_iterations:
        return GoalDecision(
            GoalAction.STOP,
            "iteration budget exhausted",
            failure_signature=signature,
        )
    return GoalDecision(
        GoalAction.CONTINUE,
        "verification or review still has blockers",
        next_prompt=(
            f"Continue working toward this objective: {spec.objective}\n\n"
            "Address only the following current blockers. Do not weaken or remove verification:\n"
            f"{failure_text[-12_000:]}"
        ),
        failure_signature=signature,
    )
