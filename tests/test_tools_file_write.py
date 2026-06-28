"""FileWrite tool tests"""

from __future__ import annotations

import tempfile
from pathlib import Path

from reasoning_agent.tools.cache import mark_as_read
from reasoning_agent.tools.file_write import write


def test_write_new_file():
    """创建新文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "test.txt")
        result = write.invoke({"file_path": f, "content": "hello world"})
        assert "created" in result
        assert Path(f).read_text() == "hello world"


def test_write_overwrite_after_read():
    """覆写已读文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "test.txt")
        Path(f).write_text("old content")
        mark_as_read(f)

        result = write.invoke({"file_path": f, "content": "new content"})
        assert "updated" in result
        assert Path(f).read_text() == "new content"


def test_write_without_read():
    """未读文件拒绝写入。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "test.txt")
        Path(f).write_text("existing")

        result = write.invoke({"file_path": f, "content": "new"})
        assert "Error" in result
        assert "not been read" in result.lower()


def test_write_parent_dir_created():
    """自动创建父目录。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "sub" / "nested" / "file.txt")
        result = write.invoke({"file_path": f, "content": "nested"})
        assert "created" in result
        assert Path(f).exists()
