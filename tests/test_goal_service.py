from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from voice_code.goals import (
    GoalExecutionAdapter,
    GoalLoop,
    GoalRuntimeService,
    GoalSpec,
    GoalState,
    GoalStatus,
    GoalStore,
)
from voice_code.goals import store as goal_store
from voice_code.goals.store import GoalLeaseConflictError
from voice_code.goals.workspace import GoalWorkspaceManager


def _init_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / ".gitignore").write_text(".reasoning/\n", encoding="utf-8")
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)


def _spec(workspace: Path, goal_id: str) -> GoalSpec:
    return GoalSpec(
        goal_id=goal_id,
        objective="update the value",
        workspace=str(workspace),
        verification_commands=[
            'python -c "from pathlib import Path; '
            "assert Path('app.py').read_text() == 'VALUE = 2\\n'\""
        ],
        allowed_paths=["app.py"],
        require_review=False,
    )


@pytest.mark.asyncio
async def test_runtime_service_starts_and_inspects_an_isolated_goal(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    spec = _spec(tmp_path, "goal-service-start")

    async def builder(request):
        (Path(request.workspace) / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return "updated"

    service = GoalRuntimeService(
        tmp_path,
        adapter_factory=lambda _spec: GoalExecutionAdapter(builder=builder),
    )

    result = await service.start(spec)

    assert result.state.status == GoalStatus.COMPLETED
    assert service.inspect(spec.goal_id).status == GoalStatus.COMPLETED
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    await GoalWorkspaceManager(tmp_path).discard(spec.goal_id)


@pytest.mark.asyncio
async def test_runtime_service_resumes_from_the_persisted_spec(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    spec = _spec(tmp_path, "goal-service-resume")

    async def interrupted_builder(request):
        raise RuntimeError(f"interrupted in {request.workspace}")

    with pytest.raises(RuntimeError, match="interrupted"):
        await GoalLoop(spec).run_isolated(interrupted_builder)

    async def resumed_builder(request):
        (Path(request.workspace) / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return "recovered"

    service = GoalRuntimeService(
        tmp_path,
        adapter_factory=lambda loaded: GoalExecutionAdapter(builder=resumed_builder)
        if loaded.goal_id == spec.goal_id
        else pytest.fail("loaded the wrong goal"),
    )

    result = await service.resume(spec.goal_id)

    assert result.state.status == GoalStatus.COMPLETED
    await GoalWorkspaceManager(tmp_path).discard(spec.goal_id)


def test_runtime_service_rejects_a_spec_for_another_workspace(tmp_path: Path) -> None:
    service = GoalRuntimeService(
        tmp_path,
        adapter_factory=lambda _spec: pytest.fail("adapter must not be created"),
    )
    spec = _spec(tmp_path / "other", "goal-wrong-workspace")

    with pytest.raises(ValueError, match="workspace"):
        service.validate_spec(spec)


def test_goal_store_lease_allows_only_one_controller(tmp_path: Path) -> None:
    store = GoalStore(tmp_path)
    spec = GoalSpec("goal-lease", "test", str(tmp_path), ["true"])
    store.create(spec, GoalState(goal_id=spec.goal_id))

    with store.acquire_lease(spec.goal_id):
        with pytest.raises(GoalLeaseConflictError, match="already running"):
            with store.acquire_lease(spec.goal_id):
                pass

    with store.acquire_lease(spec.goal_id):
        pass


def test_goal_lease_maps_windows_lock_failure_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FailingMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(_fd: int, _mode: int, _size: int) -> None:
            raise OSError("locked")

    monkeypatch.setattr(goal_store, "fcntl", None)
    monkeypatch.setattr(goal_store, "msvcrt", FailingMsvcrt)

    with pytest.raises(GoalLeaseConflictError, match="already running"):
        goal_store.GoalLease(tmp_path / "execution.lock", "goal-win")


def test_goal_store_directory_fsync_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_open(*_args, **_kwargs):
        raise OSError("directory fsync unsupported")

    monkeypatch.setattr(goal_store.os, "open", fail_open)

    goal_store.GoalStore._fsync_directory(tmp_path)


def test_stop_request_survives_a_stale_state_write(tmp_path: Path) -> None:
    store = GoalStore(tmp_path)
    spec = GoalSpec("goal-stop-marker", "test", str(tmp_path), ["true"])
    store.create(spec, GoalState(goal_id=spec.goal_id))
    stale = store.load_state(spec.goal_id)

    store.request_stop(spec.goal_id)
    store.save_state(stale)

    assert store.is_stop_requested(spec.goal_id) is True


@pytest.mark.asyncio
async def test_goal_loop_holds_the_lease_for_the_whole_execution(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    spec = _spec(tmp_path, "goal-loop-lease")
    started = asyncio.Event()
    release = asyncio.Event()

    async def builder(request):
        started.set()
        await release.wait()
        (Path(request.workspace) / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return "updated"

    running = asyncio.create_task(GoalLoop(spec).run_isolated(builder))
    await started.wait()
    try:
        with pytest.raises(GoalLeaseConflictError, match="already running"):
            await GoalLoop(spec).resume_isolated(builder)
    finally:
        release.set()

    assert (await running).state.status == GoalStatus.COMPLETED
    await GoalWorkspaceManager(tmp_path).discard(spec.goal_id)
