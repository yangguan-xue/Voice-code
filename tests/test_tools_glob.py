"""Glob tool tests"""

from __future__ import annotations

import tempfile
from pathlib import Path

from voice_code.tools.glob_search import glob


def test_glob_find_files():
    """查找匹配文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.py").write_text("")
        (Path(tmp) / "b.py").write_text("")
        (Path(tmp) / "c.txt").write_text("")

        result = glob.invoke({"pattern": "*.py", "path": tmp})
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result


def test_glob_recursive():
    """递归 glob。"""
    with tempfile.TemporaryDirectory() as tmp:
        sub = Path(tmp) / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("")
        (Path(tmp) / "root.py").write_text("")

        result = glob.invoke({"pattern": "**/*.py", "path": tmp})
        assert "root.py" in result
        assert "sub/deep.py" in result


def test_glob_no_match():
    """无匹配返回提示。"""
    with tempfile.TemporaryDirectory() as tmp:
        result = glob.invoke({"pattern": "*.xyz", "path": tmp})
        assert "No files found" in result


def test_glob_not_found():
    """不存在的目录。"""
    result = glob.invoke({"pattern": "*", "path": "/nonexistent"})
    assert "Error" in result
    assert "exact missing path" in result
