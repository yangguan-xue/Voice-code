"""Durable outer loop that advances a goal using external evidence."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path

from voice_code.goals.controller import decide_next_action
from voice_code.goals.scope import normalize_allowed_paths
from voice_code.goals.store import GoalStore
from voice_code.goals.types import (
    GoalAction,
    GoalBuildRequest,
    GoalDecision,
    GoalSpec,
    GoalState,
    GoalStatus,
)
from voice_code.goals.verifier import verify_goal
from voice_code.goals.workspace import GoalWorkspaceManager
from voice_code.security import redact_secrets, workspace_boundary
from voice_code.telemetry import MetricName, record_counter, record_histogram, start_span

Builder = Callable[[str], Awaitable[str]]
IsolatedBuilder = Callable[[GoalBuildRequest], Awaitable[str]]
Reviewer = Callable[[GoalSpec, str], Awaitable[list[str]]]
IterationBuilder = Callable[[str, GoalState], Awaitable[str]]


@dataclass(slots=True)
class GoalLoopResult:
    state: GoalState
    message: str


class GoalLoop:
    def __init__(
        self,
        spec: GoalSpec,
        *,
        store: GoalStore | None = None,
        workspace_manager: GoalWorkspaceManager | None = None,
    ) -> None:
        self.spec = spec
        self.store = store or GoalStore(spec.workspace)
        self._execution_spec = spec
        self._workspace_manager = workspace_manager

    async def run(self, builder: Builder, reviewer: Reviewer | None = None) -> GoalLoopResult:
        self._validate_reviewer(reviewer)
        now = time.time()
        state = GoalState(goal_id=self.spec.goal_id, started_at=now, updated_at=now)
        self.store.create(self.spec, state)
        self._execution_spec = self.spec

        async def iteration_builder(prompt: str, _state: GoalState) -> str:
            return await builder(prompt)

        with self.store.acquire_lease(self.spec.goal_id):
            return await self._drive(
                state,
                iteration_builder,
                reviewer,
                recover_iteration=False,
            )

    async def run_isolated(
        self,
        builder: IsolatedBuilder,
        reviewer: Reviewer | None = None,
    ) -> GoalLoopResult:
        self._validate_reviewer(reviewer)
        self._validate_isolated_scope()
        now = time.time()
        state = GoalState(goal_id=self.spec.goal_id, started_at=now, updated_at=now)
        self.store.create(self.spec, state)
        with self.store.acquire_lease(self.spec.goal_id):
            manager = self._workspace_manager or GoalWorkspaceManager(self.spec.workspace)
            self._workspace_manager = manager
            try:
                goal_workspace = await manager.prepare(self.spec.goal_id)
            except Exception as exc:
                state.status = GoalStatus.FAILED
                self._record_failure(state, str(exc))
                raise
            state.execution_workspace = str(goal_workspace.path)
            state.baseline_ref = goal_workspace.baseline_ref
            self._execution_spec = replace(
                self.spec,
                workspace=state.execution_workspace,
                baseline_ref=state.baseline_ref,
            )
            self._save(state)

            async def iteration_builder(prompt: str, current: GoalState) -> str:
                with workspace_boundary(current.execution_workspace):
                    return await builder(
                        GoalBuildRequest(
                            prompt=prompt,
                            workspace=current.execution_workspace,
                            iteration=current.iteration,
                            baseline_ref=current.baseline_ref,
                        )
                    )

            return await self._drive(
                state,
                iteration_builder,
                reviewer,
                recover_iteration=False,
            )

    async def resume(
        self,
        builder: Builder,
        reviewer: Reviewer | None = None,
    ) -> GoalLoopResult:
        self._validate_reviewer(reviewer)
        with self.store.acquire_lease(self.spec.goal_id):
            persisted_spec = self.store.load_spec(self.spec.goal_id)
            if persisted_spec.to_dict() != redact_secrets(self.spec.to_dict()):
                raise ValueError("goal specification does not match persisted specification")
            state = self.store.load_state(self.spec.goal_id)
            if state.execution_workspace:
                raise ValueError("isolated goals require the isolated resume API")
            if state.status in {
                GoalStatus.COMPLETED,
                GoalStatus.STOPPED,
                GoalStatus.WAITING_USER,
            }:
                raise ValueError(f"cannot resume goal in {state.status} state")
            if state.started_at <= 0:
                state.started_at = time.time()
            recover_iteration = state.iteration > 0 and state.status != GoalStatus.CREATED
            self._execution_spec = self.spec

            async def iteration_builder(prompt: str, _state: GoalState) -> str:
                return await builder(prompt)

            return await self._drive(
                state,
                iteration_builder,
                reviewer,
                recover_iteration=recover_iteration,
            )

    async def resume_isolated(
        self,
        builder: IsolatedBuilder,
        reviewer: Reviewer | None = None,
    ) -> GoalLoopResult:
        self._validate_reviewer(reviewer)
        self._validate_isolated_scope()
        with self.store.acquire_lease(self.spec.goal_id):
            persisted_spec = self.store.load_spec(self.spec.goal_id)
            if persisted_spec.to_dict() != redact_secrets(self.spec.to_dict()):
                raise ValueError("goal specification does not match persisted specification")
            state = self.store.load_state(self.spec.goal_id)
            if not state.execution_workspace or not state.baseline_ref:
                raise ValueError("goal does not have an isolated execution workspace")
            if state.status in {
                GoalStatus.COMPLETED,
                GoalStatus.STOPPED,
                GoalStatus.WAITING_USER,
            }:
                raise ValueError(f"cannot resume goal in {state.status} state")
            manager = self._workspace_manager or GoalWorkspaceManager(self.spec.workspace)
            self._workspace_manager = manager
            goal_workspace = await manager.attach(state.goal_id, state.baseline_ref)
            if goal_workspace.path.resolve() != Path(state.execution_workspace).resolve():
                raise ValueError("persisted execution workspace does not match managed worktree")
            self._execution_spec = replace(
                self.spec,
                workspace=state.execution_workspace,
                baseline_ref=state.baseline_ref,
            )
            recover_iteration = state.iteration > 0 and state.status != GoalStatus.CREATED

            async def iteration_builder(prompt: str, current: GoalState) -> str:
                with workspace_boundary(current.execution_workspace):
                    return await builder(
                        GoalBuildRequest(
                            prompt=prompt,
                            workspace=current.execution_workspace,
                            iteration=current.iteration,
                            baseline_ref=current.baseline_ref,
                        )
                    )

            return await self._drive(
                state,
                iteration_builder,
                reviewer,
                recover_iteration=recover_iteration,
            )

    async def _drive(
        self,
        state: GoalState,
        builder: IterationBuilder,
        reviewer: Reviewer | None,
        *,
        recover_iteration: bool,
    ) -> GoalLoopResult:
        prompt = state.last_prompt or self.spec.objective

        try:
            while True:
                if self._remaining_seconds(state) <= 0:
                    raise TimeoutError
                if self.store.is_stop_requested(state.goal_id):
                    state.stop_requested = True
                    state.status = GoalStatus.STOPPED
                    return self._finish(state, "stop requested")

                if recover_iteration:
                    result = state.last_result
                    recover_iteration = False
                else:
                    if state.iteration >= self.spec.max_iterations:
                        state.status = GoalStatus.STOPPED
                        return self._finish(state, "iteration budget exhausted")
                    state.iteration += 1
                    state.status = GoalStatus.BUILDING
                    state.last_prompt = prompt
                    self._save(state)
                    stage_started = time.perf_counter()
                    try:
                        with start_span("goal.build", {"stage": "build"}):
                            async with asyncio.timeout(self._remaining_seconds(state)):
                                result = await builder(prompt, state)
                    except Exception:
                        self._record_iteration_metric("build", "error", stage_started)
                        raise
                    self._record_iteration_metric("build", "success", stage_started)
                    state.last_result = result

                decision = await self._evaluate_iteration(state, result, reviewer)

                if decision.action == GoalAction.FINISH:
                    if self._workspace_manager is not None and state.execution_workspace:
                        state.last_checkpoint_ref = await self._workspace_manager.checkpoint(
                            state.goal_id,
                            iteration=state.iteration,
                        )
                    state.status = GoalStatus.COMPLETED
                    return self._finish(state, decision.reason)
                if decision.action == GoalAction.ESCALATE:
                    state.status = GoalStatus.WAITING_USER
                    return self._finish(state, decision.reason)
                if decision.action == GoalAction.ROLLBACK:
                    if self._workspace_manager is None or not state.baseline_ref:
                        state.status = GoalStatus.STOPPED
                        return self._finish(state, decision.reason)
                    checkpoint = state.last_checkpoint_ref or state.baseline_ref
                    restored = await self._workspace_manager.restore(state.goal_id, checkpoint)
                    state.execution_workspace = str(restored.path)
                    prompt = decision.next_prompt or self.spec.objective
                    self._save(state)
                    continue
                if decision.action == GoalAction.STOP:
                    state.status = GoalStatus.STOPPED
                    return self._finish(state, decision.reason)
                prompt = decision.next_prompt
                self._save(state)
        except TimeoutError:
            state.status = GoalStatus.STOPPED
            return self._finish(state, "time budget exhausted")
        except asyncio.CancelledError:
            state.status = GoalStatus.INTERRUPTED
            self._record_failure(state, "cancelled")
            raise
        except Exception as exc:
            state.status = GoalStatus.FAILED
            self._record_failure(state, str(exc))
            raise

    async def _evaluate_iteration(
        self,
        state: GoalState,
        result: str,
        reviewer: Reviewer | None,
    ) -> GoalDecision:
        state.status = GoalStatus.VERIFYING
        self._save(state)
        async with asyncio.timeout(self._remaining_seconds(state)):
            evidence = await verify_goal(self._execution_spec)

        blockers: list[str] = []
        if reviewer is not None and self.spec.require_review:
            state.status = GoalStatus.REVIEWING
            self._save(state)
            async with asyncio.timeout(self._remaining_seconds(state)):
                blockers = await reviewer(self._execution_spec, result)

        state.status = GoalStatus.DECIDING
        decision = decide_next_action(
            self.spec,
            state,
            evidence,
            reviewer_blockers=blockers,
        )
        if decision.failure_signature == state.last_failure_signature:
            state.repeated_failure_count += 1
        elif decision.failure_signature:
            state.repeated_failure_count = 1
        state.last_failure_signature = decision.failure_signature
        evidence_name = self.store.write_evidence(
            state.goal_id,
            state.iteration,
            {
                "builder_result": result,
                "verification": [item.to_dict() for item in evidence],
                "reviewer_blockers": blockers,
                "decision": decision.to_dict(),
            },
        )
        state.history.append(
            {
                "iteration": state.iteration,
                "evidence": evidence_name,
                "decision": str(decision.action),
                "reason": decision.reason,
            }
        )
        return decision

    def _validate_reviewer(self, reviewer: Reviewer | None) -> None:
        if self.spec.require_review and reviewer is None:
            raise ValueError("reviewer is required by the goal specification")

    def _validate_isolated_scope(self) -> None:
        if not self.spec.allowed_paths:
            raise ValueError("isolated goals require at least one allowed path")
        normalize_allowed_paths(self.spec.allowed_paths)

    def _remaining_seconds(self, state: GoalState) -> float:
        return self.spec.max_seconds - (time.time() - state.started_at)

    @staticmethod
    def _record_iteration_metric(stage: str, outcome: str, started: float) -> None:
        record_counter(
            MetricName.GOAL_ITERATIONS_TOTAL,
            attributes={"stage": stage, "outcome": outcome},
        )
        record_histogram(
            MetricName.GOAL_ITERATION_DURATION_SECONDS,
            time.perf_counter() - started,
            attributes={"stage": stage},
        )

    def _record_failure(self, state: GoalState, reason: str) -> None:
        state.history.append(
            {
                "iteration": state.iteration,
                "decision": "fail",
                "reason": reason,
            }
        )
        self._save(state)

    def _save(self, state: GoalState) -> None:
        state.updated_at = time.time()
        latest = state.history[-1] if state.history else {}
        evidence = latest.get("evidence")
        self.store.save_state(
            state,
            reason_code=str(latest.get("decision", "")).upper(),
            evidence_refs=[str(evidence)] if evidence else [],
        )
        self.store.write_progress(self.spec, state)

    def _finish(self, state: GoalState, message: str) -> GoalLoopResult:
        self._save(state)
        return GoalLoopResult(state=state, message=message)
