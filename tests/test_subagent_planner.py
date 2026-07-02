from __future__ import annotations

from voice_code.subagents.planner import AgentToolRequest, SpawnMode, plan_spawn


def test_plan_fresh_sync():
    plan = plan_spawn(
        AgentToolRequest(
            description="Run reviewer",
            prompt="Review the patch",
            subagent_type="reviewer",
            run_in_background=False,
        )
    )

    assert plan.mode == SpawnMode.FRESH_SYNC
    assert plan.count == 1
    assert plan.agent_type == "reviewer"


def test_plan_fork_async_when_subagent_type_omitted():
    plan = plan_spawn(
        AgentToolRequest(
            description="Fork",
            prompt="Investigate deeply",
            subagent_type=None,
            run_in_background=True,
        )
    )

    assert plan.mode == SpawnMode.FORK_ASYNC
    assert plan.agent_type == "fork"


def test_plan_parallel_fresh():
    plan = plan_spawn(
        AgentToolRequest(
            description="Run 3 researchers",
            prompt="Search in parallel",
            subagent_type="researcher",
            run_in_background=True,
            count=3,
        )
    )

    assert plan.mode == SpawnMode.PARALLEL_FRESH
    assert plan.count == 3


def test_plan_rejects_invalid_count():
    try:
        plan_spawn(
            AgentToolRequest(
                description="Bad",
                prompt="Bad",
                subagent_type="researcher",
                count=0,
            )
        )
    except ValueError as exc:
        assert "count" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected invalid count to fail")
