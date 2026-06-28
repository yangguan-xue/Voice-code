"""上下文管理测试"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from voice_code.context import get_context, get_git_status, load_claude_md


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
# CLAUDE.md 测试
# ============================================================


@pytest.mark.asyncio
async def test_load_claude_md_empty():
    """无 CLAUDE.md → 返回空字符串。"""
    with tempfile.TemporaryDirectory() as tmp:
        result = await load_claude_md(tmp)
    assert result == ""


@pytest.mark.asyncio
async def test_load_claude_md_single():
    """存在 CLAUDE.md → 返回内容。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "CLAUDE.md").write_text("Always use pandas.")
        result = await load_claude_md(tmp)
    assert "Always use pandas." in result
    assert "CLAUDE.md" in result


@pytest.mark.asyncio
async def test_load_claude_md_multiple():
    """多个文件 → 按优先级拼接。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "CLAUDE.md").write_text("Project: use pandas.")
        (Path(tmp) / ".claude").mkdir()
        (Path(tmp) / ".claude" / "CLAUDE.md").write_text("Config: use black.")
        (Path(tmp) / "CLAUDE.local.md").write_text("Local: use mypy.")

        result = await load_claude_md(tmp)

    assert "Project: use pandas." in result
    assert "Config: use black." in result
    assert "Local: use mypy." in result
    assert result.find("CLAUDE.md (project") < result.find(".claude/CLAUDE.md")
    assert result.find(".claude/CLAUDE.md") < result.find("CLAUDE.local.md")


@pytest.mark.asyncio
async def test_load_claude_md_empty_cwd():
    """空 CWD → 使用当前目录，不崩溃。"""
    result = await load_claude_md("")
    assert isinstance(result, str)


# ============================================================
# get_context 集成测试
# ============================================================


@pytest.mark.asyncio
async def test_get_context():
    """get_context 返回完整 dict。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "CLAUDE.md").write_text("Use pytest.")
        result = await get_context(tmp)

    assert "claudeMd" in result
    assert "gitStatus" in result
    assert "currentDate" in result
    assert "Use pytest." in result["claudeMd"]
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
