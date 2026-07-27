from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from voice_code.goals import GoalLoop, GoalSpec, GoalStore
from voice_code.goals.types import (
    GoalAction,
    GoalBuildRequest,
    GoalDecision,
    GoalStatus,
)
from voice_code.goals.workspace import GoalWorkspaceManager
from voice_code.security import get_workspace_root


def _init_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=path,
        check=True,
    )
    (path / ".gitignore").write_text(".reasoning/\n", encoding="utf-8")
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)


@pytest.mark.asyncio
async def test_prepare_isolates_goal_from_dirty_primary_workspace(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    (tmp_path / "app.py").write_text("USER CHANGE\n", encoding="utf-8")
    manager = GoalWorkspaceManager(tmp_path)

    workspace = await manager.prepare("goal-1")
    (workspace.path / "app.py").write_text("AGENT CHANGE\n", encoding="utf-8")

    assert workspace.baseline_ref
    assert workspace.path == tmp_path / ".reasoning" / "worktrees" / "goal-1"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "USER CHANGE\n"
    assert (workspace.path / "app.py").read_text(encoding="utf-8") == "AGENT CHANGE\n"
    await manager.discard("goal-1")


@pytest.mark.asyncio
async def test_checkpoint_and_restore_recreate_only_the_goal_worktree(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    manager = GoalWorkspaceManager(tmp_path)
    workspace = await manager.prepare("goal-restore")
    (workspace.path / "app.py").write_text("KNOWN GOOD\n", encoding="utf-8")
    checkpoint = await manager.checkpoint("goal-restore", iteration=1)
    (workspace.path / "app.py").write_text("REGRESSION\n", encoding="utf-8")
    (workspace.path / "untracked.txt").write_text("remove me\n", encoding="utf-8")

    restored = await manager.restore("goal-restore", checkpoint)

    assert restored.path == workspace.path
    assert (restored.path / "app.py").read_text(encoding="utf-8") == "KNOWN GOOD\n"
    assert not (restored.path / "untracked.txt").exists()
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    await manager.discard("goal-restore")


@pytest.mark.asyncio
async def test_prepare_rejects_an_escaping_goal_id(tmp_path: Path) -> None:
    _init_repository(tmp_path)

    with pytest.raises(ValueError, match="goal id"):
        await GoalWorkspaceManager(tmp_path).prepare("../escape")


def test_manager_rejects_a_symlinked_state_directory(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / ".reasoning").symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="symlink"):
            GoalWorkspaceManager(tmp_path)
    finally:
        (tmp_path / ".reasoning").unlink()
        shutil.rmtree(outside)


@pytest.mark.asyncio
async def test_prepare_preserves_a_workspace_relative_to_repository_root(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / ".gitignore").write_text(".reasoning/\n", encoding="utf-8")
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    workspace = await GoalWorkspaceManager(project).prepare("goal-subdir")

    assert workspace.path == project / ".reasoning" / "worktrees" / "goal-subdir" / "project"
    assert (workspace.path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    await GoalWorkspaceManager(project).discard("goal-subdir")


@pytest.mark.asyncio
async def test_isolated_goal_completes_without_modifying_primary_workspace(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    spec = GoalSpec(
        goal_id="goal-isolated",
        objective="update the value",
        workspace=str(tmp_path),
        verification_commands=[
            'python -c "from pathlib import Path; '
            "assert Path('app.py').read_text() == 'VALUE = 2\\n'\""
        ],
        allowed_paths=["app.py"],
        require_review=False,
    )
    received_request: GoalBuildRequest | None = None

    async def builder(request: GoalBuildRequest) -> str:
        nonlocal received_request
        received_request = request
        assert get_workspace_root() == Path(request.workspace)
        (Path(request.workspace) / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return "updated"

    result = await GoalLoop(spec).run_isolated(builder)

    assert result.state.status == GoalStatus.COMPLETED
    assert received_request is not None
    assert Path(received_request.workspace) != tmp_path
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert result.state.baseline_ref
    assert result.state.last_checkpoint_ref
    assert Path(result.state.execution_workspace).is_dir()
    await GoalWorkspaceManager(tmp_path).discard(spec.goal_id)


@pytest.mark.asyncio
async def test_isolated_goal_scope_violation_cannot_complete(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    spec = GoalSpec(
        goal_id="goal-scope-stop",
        objective="do not touch tests",
        workspace=str(tmp_path),
        verification_commands=["true"],
        allowed_paths=["app.py"],
        max_iterations=1,
        require_review=False,
    )

    async def builder(request: GoalBuildRequest) -> str:
        (Path(request.workspace) / "tests.py").write_text("bad\n", encoding="utf-8")
        return "done"

    result = await GoalLoop(spec).run_isolated(builder)

    assert result.state.status == GoalStatus.STOPPED
    evidence_name = result.state.history[-1]["evidence"]
    evidence = (
        GoalStore(tmp_path).goal_dir(spec.goal_id) / "evidence" / evidence_name
    ).read_text(encoding="utf-8")
    assert "tests.py" in evidence
    assert not (tmp_path / "tests.py").exists()
    await GoalWorkspaceManager(tmp_path).discard(spec.goal_id)


@pytest.mark.asyncio
async def test_rollback_restores_baseline_before_the_next_builder_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repository(tmp_path)
    spec = GoalSpec(
        goal_id="goal-rollback",
        objective="recover from regression",
        workspace=str(tmp_path),
        verification_commands=["true"],
        allowed_paths=["app.py"],
        max_iterations=2,
        require_review=False,
    )
    decisions = iter(
        [
            GoalDecision(GoalAction.ROLLBACK, "regression", next_prompt="retry safely"),
            GoalDecision(GoalAction.FINISH, "recovered"),
        ]
    )
    monkeypatch.setattr(
        "voice_code.goals.loop.decide_next_action",
        lambda *_args, **_kwargs: next(decisions),
    )
    second_iteration_started_from_baseline = False

    async def builder(request: GoalBuildRequest) -> str:
        nonlocal second_iteration_started_from_baseline
        path = Path(request.workspace) / "app.py"
        if request.iteration == 1:
            path.write_text("REGRESSION\n", encoding="utf-8")
        else:
            second_iteration_started_from_baseline = (
                path.read_text(encoding="utf-8") == "VALUE = 1\n"
            )
            path.write_text("VALUE = 2\n", encoding="utf-8")
        return "iteration complete"

    result = await GoalLoop(spec).run_isolated(builder)

    assert result.state.status == GoalStatus.COMPLETED
    assert second_iteration_started_from_baseline is True
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    await GoalWorkspaceManager(tmp_path).discard(spec.goal_id)


@pytest.mark.asyncio
async def test_failed_isolated_goal_resumes_without_repeating_its_builder(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    spec = GoalSpec(
        goal_id="goal-resume-isolated",
        objective="persist the completed edit",
        workspace=str(tmp_path),
        verification_commands=[
            f'"{sys.executable}" -c "from pathlib import Path; '
            "assert Path('app.py').read_text(encoding='utf-8') == 'VALUE = 2\\n'\""
        ],
        allowed_paths=["app.py"],
        require_review=False,
    )

    async def failing_builder(request: GoalBuildRequest) -> str:
        (Path(request.workspace) / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        raise RuntimeError("process crashed after writing")

    with pytest.raises(RuntimeError, match="process crashed"):
        await GoalLoop(spec).run_isolated(failing_builder)

    resumed_builder_called = False

    async def resumed_builder(_request: GoalBuildRequest) -> str:
        nonlocal resumed_builder_called
        resumed_builder_called = True
        return "should not repeat"

    result = await GoalLoop(spec).resume_isolated(resumed_builder)

    assert result.state.status == GoalStatus.COMPLETED
    assert resumed_builder_called is False
    assert result.state.last_checkpoint_ref
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    await GoalWorkspaceManager(tmp_path).discard(spec.goal_id)


@pytest.mark.asyncio
async def test_invalid_scope_is_rejected_before_isolated_builder_runs(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    spec = GoalSpec(
        goal_id="goal-invalid-scope",
        objective="escape",
        workspace=str(tmp_path),
        verification_commands=["true"],
        allowed_paths=["../outside"],
        require_review=False,
    )
    builder_called = False

    async def builder(_request: GoalBuildRequest) -> str:
        nonlocal builder_called
        builder_called = True
        return "unsafe"

    with pytest.raises(ValueError, match="allowed path"):
        await GoalLoop(spec).run_isolated(builder)

    assert builder_called is False
    assert not (tmp_path / ".reasoning" / "worktrees" / spec.goal_id).exists()
