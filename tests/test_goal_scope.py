from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from voice_code.goals import GoalSpec
from voice_code.goals.scope import normalize_allowed_paths
from voice_code.goals.verifier import verify_goal


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
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_app.py").write_text("def test_app(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)


def _spec(workspace: Path, allowed_paths: list[str]) -> GoalSpec:
    return GoalSpec(
        goal_id="goal-scope",
        objective="change only src",
        workspace=str(workspace),
        verification_commands=["true"],
        allowed_paths=allowed_paths,
        require_review=False,
    )


def _init_monorepo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / ".gitignore").write_text(".reasoning/\n", encoding="utf-8")
    project = path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_app.py").write_text("def test_app(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)
    return project


def test_normalize_allowed_paths_accepts_windows_relative_separators() -> None:
    assert normalize_allowed_paths([r"src\app.py", r"tests\unit"]) == (
        "src/app.py",
        "tests/unit",
    )


@pytest.mark.parametrize("allowed_path", [r"C:\outside", r"D:/outside", r"\server\share"])
def test_normalize_allowed_paths_rejects_windows_absolute_paths(allowed_path: str) -> None:
    with pytest.raises(ValueError, match="allowed path"):
        normalize_allowed_paths([allowed_path])


@pytest.mark.asyncio
async def test_scope_gate_passes_when_all_changes_are_allowed(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    (tmp_path / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    evidence = await verify_goal(_spec(tmp_path, ["src"]))

    scope = next(item for item in evidence if item.command == "git diff --scope")
    assert scope.passed is True
    assert "src/app.py" in scope.stdout


@pytest.mark.asyncio
async def test_scope_gate_blocks_tracked_and_untracked_changes_outside_scope(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    (tmp_path / "tests" / "test_app.py").write_text("def test_bad(): pass\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("untracked\n", encoding="utf-8")

    evidence = await verify_goal(_spec(tmp_path, ["src"]))

    scope = next(item for item in evidence if item.command == "git diff --scope")
    assert scope.passed is False
    assert "notes.txt" in scope.stderr
    assert "tests/test_app.py" in scope.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed_path", [".", "./"])
async def test_scope_gate_allows_workspace_root_scope(
    tmp_path: Path,
    allowed_path: str,
) -> None:
    _init_repository(tmp_path)
    (tmp_path / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text("def test_changed(): pass\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("untracked\n", encoding="utf-8")

    evidence = await verify_goal(_spec(tmp_path, [allowed_path]))

    scope = next(item for item in evidence if item.command == "git diff --scope")
    assert scope.passed is True
    assert "src/app.py" in scope.stdout
    assert "tests/test_app.py" in scope.stdout
    assert "notes.txt" in scope.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed_path", ["../outside", "/tmp/outside", "src/../../tests"])
async def test_scope_gate_rejects_escaping_allowed_paths(
    tmp_path: Path,
    allowed_path: str,
) -> None:
    _init_repository(tmp_path)

    with pytest.raises(ValueError, match="allowed path"):
        await verify_goal(_spec(tmp_path, [allowed_path]))


@pytest.mark.asyncio
async def test_scope_gate_rejects_an_untrusted_baseline_revision(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    spec = _spec(tmp_path, ["src"])
    spec.baseline_ref = "--no-index"

    with pytest.raises(ValueError, match="baseline"):
        await verify_goal(spec)


@pytest.mark.asyncio
async def test_scope_gate_runs_after_mutating_verification_commands(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    spec = _spec(tmp_path, ["src"])
    spec.verification_commands = [
        f'"{sys.executable}" -c "from pathlib import Path; '
        "Path('tests/created-by-verifier.py').write_text('created\\n', encoding='utf-8')\""
    ]

    evidence = await verify_goal(spec)

    assert evidence[-1].command == "git diff --scope"
    assert evidence[-1].passed is False
    assert "tests/created-by-verifier.py" in evidence[-1].stderr


@pytest.mark.asyncio
async def test_scope_gate_maps_repository_paths_to_a_subdirectory_workspace(
    tmp_path: Path,
) -> None:
    project = _init_monorepo(tmp_path)
    (project / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    evidence = await verify_goal(_spec(project, ["src"]))

    scope = next(item for item in evidence if item.command == "git diff --scope")
    assert scope.passed is True
    assert "src/app.py" in scope.stdout


@pytest.mark.asyncio
async def test_scope_gate_blocks_untracked_changes_outside_subdirectory_workspace(
    tmp_path: Path,
) -> None:
    project = _init_monorepo(tmp_path)
    (tmp_path / "outside.txt").write_text("escape\n", encoding="utf-8")

    evidence = await verify_goal(_spec(project, ["src"]))

    scope = next(item for item in evidence if item.command == "git diff --scope")
    assert scope.passed is False
    assert "outside.txt" in scope.stderr


@pytest.mark.asyncio
async def test_scope_gate_root_scope_stays_inside_subdirectory_workspace(
    tmp_path: Path,
) -> None:
    project = _init_monorepo(tmp_path)
    (project / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("escape\n", encoding="utf-8")

    evidence = await verify_goal(_spec(project, ["."]))

    scope = next(item for item in evidence if item.command == "git diff --scope")
    assert scope.passed is False
    assert "src/app.py" in scope.stdout
    assert "outside.txt" in scope.stderr
