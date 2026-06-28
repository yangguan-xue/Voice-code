"""Bash tool tests"""

from __future__ import annotations

from voice_code.tools.bash import bash


def test_bash_simple():
    """简单命令返回输出。"""
    result = bash.invoke({"command": "echo hello"})
    assert "hello" in result


def test_bash_error():
    """失败命令返回错误。"""
    result = bash.invoke({"command": "exit 1"})
    assert "Exit code" in result


def test_bash_command_not_found():
    """不存在的命令返回输出信息。"""
    result = bash.invoke({"command": "nonexistent_command_xyz"})
    assert len(result) > 0
