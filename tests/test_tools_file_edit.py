"""FileEdit tool tests"""

from __future__ import annotations

import tempfile
from pathlib import Path

from voice_code.tools.cache import mark_as_read
from voice_code.tools.file_edit import edit


def test_edit_single():
    """单次替换。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "test.py")
        Path(f).write_text("hello world\nfoo bar\n")
        mark_as_read(f)

        result = edit.invoke({
            "file_path": f,
            "old_string": "hello world",
            "new_string": "hi there",
        })
        assert "replaced" in result.lower()
        assert Path(f).read_text() == "hi there\nfoo bar\n"


def test_edit_replace_all():
    """全局替换。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "test.py")
        Path(f).write_text("foo\nbar\nfoo\n")
        mark_as_read(f)

        result = edit.invoke({
            "file_path": f,
            "old_string": "foo",
            "new_string": "baz",
            "replace_all": True,
        })
        assert "all occurrences" in result.lower()
        assert Path(f).read_text() == "baz\nbar\nbaz\n"


def test_edit_multiple_without_replace_all():
    """多次出现但不允许 replace_all → 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "test.py")
        Path(f).write_text("foo\nbar\nfoo\n")
        mark_as_read(f)

        result = edit.invoke({
            "file_path": f,
            "old_string": "foo",
            "new_string": "baz",
        })
        assert "Error" in result
        assert "2 occurrences" in result


def test_edit_not_found():
    """old_string 不存在。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "test.py")
        Path(f).write_text("something else")
        mark_as_read(f)

        result = edit.invoke({
            "file_path": f,
            "old_string": "not here",
            "new_string": "x",
        })
        assert "Error" in result
        assert "not found" in result


def test_edit_noop():
    """相同字符串 → 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "test.py")
        Path(f).write_text("hello")
        mark_as_read(f)

        result = edit.invoke({
            "file_path": f,
            "old_string": "hello",
            "new_string": "hello",
        })
        assert "Error" in result


def test_edit_without_read():
    """未读文件拒绝编辑。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "test.py")
        Path(f).write_text("hello")

        result = edit.invoke({
            "file_path": f,
            "old_string": "hello",
            "new_string": "bye",
        })
        assert "Error" in result
        assert "not been read" in result.lower()


def test_edit_create_new_file():
    """空 old_string + 不存在的文件 → 创建新文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = str(Path(tmp) / "new.py")
        result = edit.invoke({
            "file_path": f,
            "old_string": "",
            "new_string": "print('hello')",
        })
        assert "created" in result
        assert Path(f).read_text() == "print('hello')"
