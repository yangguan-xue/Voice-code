"""Agent tool -> spawn plan 编译。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SpawnMode(StrEnum):
    FRESH_SYNC = "fresh_sync"
    FRESH_ASYNC = "fresh_async"
    FORK_ASYNC = "fork_async"
    PARALLEL_FRESH = "parallel_fresh"
    PARALLEL_FORK = "parallel_fork"


@dataclass(slots=True)
class AgentToolRequest:
    description: str
    prompt: str
    subagent_type: str | None = None
    model: str | None = None
    run_in_background: bool | None = None
    count: int = 1


@dataclass(slots=True)
class SpawnPlan:
    mode: SpawnMode
    agent_type: str
    description: str
    prompt: str
    model: str | None
    run_in_background: bool
    count: int


def plan_spawn(request: AgentToolRequest) -> SpawnPlan:
    if request.count < 1:
        raise ValueError("count must be >= 1")

    is_fork = request.subagent_type is None
    agent_type = request.subagent_type or "fork"

    if request.count > 1:
        return SpawnPlan(
            mode=SpawnMode.PARALLEL_FORK if is_fork else SpawnMode.PARALLEL_FRESH,
            agent_type=agent_type,
            description=request.description,
            prompt=request.prompt,
            model=request.model,
            run_in_background=True,
            count=request.count,
        )

    if is_fork:
        return SpawnPlan(
            mode=SpawnMode.FORK_ASYNC,
            agent_type=agent_type,
            description=request.description,
            prompt=request.prompt,
            model=request.model,
            run_in_background=True,
            count=1,
        )

    run_in_background = bool(request.run_in_background)
    return SpawnPlan(
        mode=SpawnMode.FRESH_ASYNC if run_in_background else SpawnMode.FRESH_SYNC,
        agent_type=agent_type,
        description=request.description,
        prompt=request.prompt,
        model=request.model,
        run_in_background=run_in_background,
        count=1,
    )
