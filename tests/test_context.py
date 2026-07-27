"""上下文管理测试"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from voice_code.context import get_context, get_git_status, load_project_instructions

# ============================================================
# Git Status 测试
# ============================================================


@pytest.mark.asyncio
async def test_get_git_status_non_git_dir():
    """非 git 目录 → 返回 None。"""
    with tempfile.TemporaryDirectory() as tmp:
        result = await get_git_status(tmp)
    assert result is None


@pytest.mark.asyncio
async def test_get_git_status_in_git_dir():
    """git 目录 → 返回格式化字符串。"""
    with tempfile.TemporaryDirectory() as tmp:
        _run_sync(["git", "init"], tmp)
        _run_sync(["git", "config", "user.email", "test@test.com"], tmp)
        _run_sync(["git", "config", "user.name", "Test"], tmp)
        # Create initial commit so log -5 works
        (Path(tmp) / "README.md").write_text("hello")
        _run_sync(["git", "add", "."], tmp)
        _run_sync(["git", "commit", "-m", "init"], tmp)

        result = await get_git_status(tmp)

    assert result is not None
    assert "Current branch:" in result
    assert "Status:" in result
    assert "Recent commits:" in result
    assert "(clean)" in result


@pytest.mark.asyncio
async def test_get_git_status_with_dirty_worktree():
    """有未暂存文件 → status 显示文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        _run_sync(["git", "init"], tmp)
        _run_sync(["git", "config", "user.email", "test@test.com"], tmp)
        _run_sync(["git", "config", "user.name", "Test"], tmp)
        (Path(tmp) / "a.txt").write_text("hello")
        _run_sync(["git", "add", "."], tmp)
        _run_sync(["git", "commit", "-m", "init"], tmp)
        (Path(tmp) / "a.txt").write_text("modified")

        result = await get_git_status(tmp)

    assert result is not None
    assert "a.txt" in result


# ============================================================
# AGENTS.md 测试
# ============================================================


@pytest.mark.asyncio
async def test_load_project_instructions_empty():
    """无 AGENTS.md → 返回空字符串。"""
    with tempfile.TemporaryDirectory() as tmp:
        result = await load_project_instructions(tmp)
    assert result == ""


@pytest.mark.asyncio
async def test_load_project_instructions_single():
    """存在 AGENTS.md → 返回内容。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "AGENTS.md").write_text("Always use pandas.")
        result = await load_project_instructions(tmp)
    assert "Always use pandas." in result
    assert "AGENTS.md" in result


@pytest.mark.asyncio
async def test_load_project_instructions_multiple():
    """多个文件 → 按优先级拼接。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "AGENTS.md").write_text("Project: use pandas.")
        (Path(tmp) / ".agents").mkdir()
        (Path(tmp) / ".agents" / "AGENTS.md").write_text("Config: use black.")
        (Path(tmp) / "AGENTS.local.md").write_text("Local: use mypy.")

        result = await load_project_instructions(tmp)

    assert "Project: use pandas." in result
    assert "Config: use black." in result
    assert "Local: use mypy." in result
    assert result.find("AGENTS.md (project") < result.find(".agents/AGENTS.md")
    assert result.find(".agents/AGENTS.md") < result.find("AGENTS.local.md")


@pytest.mark.asyncio
async def test_load_project_instructions_empty_cwd():
    """空 CWD → 使用当前目录，不崩溃。"""
    result = await load_project_instructions("")
    assert isinstance(result, str)


# ============================================================
# get_context 集成测试
# ============================================================


@pytest.mark.asyncio
async def test_get_context():
    """get_context 返回完整 dict。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "AGENTS.md").write_text("Use pytest.")
        result = await get_context(tmp)

    assert "projectInstructions" in result
    assert "gitStatus" in result
    assert "currentDate" in result
    assert "Use pytest." in result["projectInstructions"]
    assert "Today's date" in result["currentDate"]


# ============================================================
# Helpers
# ============================================================


def _run_sync(cmd: list[str], cwd: str) -> str:
    """同步运行命令（用于测试 fixture 设置）。"""
    import subprocess

    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True
    ).stdout.strip()
