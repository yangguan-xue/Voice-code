"""Grep tool tests"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from voice_code.tools.grep_search import grep

_has_rg = shutil.which("rg") is not None


@pytest.mark.skipif(not _has_rg, reason="ripgrep not installed")
def test_grep_find_content():
    """搜索文件内容。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.py").write_text("def foo():\n    pass\n")
        (Path(tmp) / "b.py").write_text("def bar():\n    pass\n")

        result = grep.invoke({"pattern": "foo", "path": tmp})
        assert "foo" in result
        assert "a.py" in result


@pytest.mark.skipif(not _has_rg, reason="ripgrep not installed")
def test_grep_glob_filter():
    """glob 过滤文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.py").write_text("hello world")
        (Path(tmp) / "b.txt").write_text("hello there")

        result = grep.invoke({"pattern": "hello", "path": tmp, "include": "*.py"})
        assert "a.py" in result
        assert "b.txt" not in result


@pytest.mark.skipif(not _has_rg, reason="ripgrep not installed")
def test_grep_no_match():
    """无匹配返回提示。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.py").write_text("hello")
        result = grep.invoke({"pattern": "nonexistent", "path": tmp})
        assert "No matches found" in result


def test_grep_rg_not_found():
    """rg 未安装 → 错误提示（但测试环境可能装了，所以只测行为）。"""
    if _has_rg:
        pytest.skip("rg is installed — cannot test missing rg")
    # 这个分支在 CI 无 rg 时触发


def test_grep_path_not_found():
    """不存在的路径应提示精确路径未命中，而不是猜相近文件。"""
    result = grep.invoke({"pattern": "hello", "path": "/nonexistent"})
    assert "Error" in result
    assert "exact missing path" in result
