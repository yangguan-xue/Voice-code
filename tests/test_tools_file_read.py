"""FileRead tool tests"""

from __future__ import annotations

import tempfile
from pathlib import Path

from reasoning_agent.tools.file_read import read


def test_read_file():
    """读取文件返回内容。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line 1\nline 2\nline 3\n")
        tmp = f.name
    try:
        result = read.invoke({"file_path": tmp})
        assert "line 1" in result
        assert "line 2" in result
        assert "1:" in result  # line numbers
    finally:
        Path(tmp).unlink()


def test_read_offset():
    """offset 从指定行开始。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("a\nb\nc\nd\ne\n")
        tmp = f.name
    try:
        result = read.invoke({"file_path": tmp, "offset": 3})
        # 内容应该从第 3 行开始
        content_lines = result.split("<system-reminder>")[0]
        assert "3: c" in content_lines
        assert "1:" not in content_lines
        assert "2:" not in content_lines
    finally:
        Path(tmp).unlink()


def test_read_limit():
    """limit 限制行数。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("\n".join(str(i) for i in range(100)) + "\n")
        tmp = f.name
    try:
        result = read.invoke({"file_path": tmp, "limit": 5})
        lines = [line for line in result.split("\n") if line and line[0].isdigit()]
        assert len(lines) <= 5
    finally:
        Path(tmp).unlink()


def test_read_file_not_found():
    """不存在的文件返回错误。"""
    result = read.invoke({"file_path": "/nonexistent/file.txt"})
    assert "Error" in result
    assert "exact missing path" in result


def test_read_directory():
    """目录返回文件列表。"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.txt").write_text("a")
        (Path(tmp) / "b.txt").write_text("b")
        result = read.invoke({"file_path": tmp})
        assert "a.txt" in result
        assert "b.txt" in result
