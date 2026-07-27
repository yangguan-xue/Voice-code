"""Stable application boundary for durable isolated goal execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from voice_code.goals.loop import (
    GoalLoop,
    GoalLoopResult,
    IsolatedBuilder,
    Reviewer,
)
from voice_code.goals.store import GoalStore
from voice_code.goals.types import GoalSpec, GoalState


@dataclass(frozen=True, slots=True)
class GoalExecutionAdapter:
    """Application-provided build and review operations for one goal."""

    builder: IsolatedBuilder
    reviewer: Reviewer | None = None


GoalAdapterFactory = Callable[[GoalSpec], GoalExecutionAdapter]


class GoalRuntimeService:
    """Owns goal lifecycle operations without depending on a specific UI."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        adapter_factory: GoalAdapterFactory | None = None,
        store: GoalStore | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.store = store or GoalStore(self.workspace)
        self._adapter_factory = adapter_factory

    def validate_spec(self, spec: GoalSpec) -> None:
        if Path(spec.workspace).resolve() != self.workspace:
            raise ValueError("goal workspace does not match runtime workspace")

    async def start(self, spec: GoalSpec) -> GoalLoopResult:
        self.validate_spec(spec)
        adapter = self._execution_adapter(spec)
        return await GoalLoop(spec, store=self.store).run_isolated(
            adapter.builder,
            adapter.reviewer,
        )

    async def resume(self, goal_id: str) -> GoalLoopResult:
        spec = self.store.load_spec(goal_id)
        self.validate_spec(spec)
        adapter = self._execution_adapter(spec)
        return await GoalLoop(spec, store=self.store).resume_isolated(
            adapter.builder,
            adapter.reviewer,
        )

    def inspect(self, goal_id: str) -> GoalState:
        return self.store.load_state(goal_id)

    def load_spec(self, goal_id: str) -> GoalSpec:
        spec = self.store.load_spec(goal_id)
        self.validate_spec(spec)
        return spec

    def request_stop(self, goal_id: str) -> GoalState:
        return self.store.request_stop(goal_id)

    def _execution_adapter(self, spec: GoalSpec) -> GoalExecutionAdapter:
        if self._adapter_factory is None:
            raise RuntimeError("goal execution adapter is not configured")
        return self._adapter_factory(spec)
