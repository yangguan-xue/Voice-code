"""Bash tool tests"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from voice_code.security import workspace_boundary
from voice_code.tools.bash import BashSandboxPolicy, bash

bash_module = import_module("voice_code.tools.bash")


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


def test_bash_rejects_absolute_workdir_bypass(tmp_path: Path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    with workspace_boundary(allowed):
        result = bash.invoke({"command": "pwd", "workdir": str(outside)})

    assert "outside the active workspace" in result


def test_bash_rejects_symlink_workdir_escape(tmp_path: Path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    link = allowed / "link"
    link.symlink_to(outside, target_is_directory=True)

    with workspace_boundary(allowed):
        result = bash.invoke({"command": "pwd", "workdir": "link"})

    assert "outside the active workspace" in result


def test_bash_policy_rejects_absolute_allowlist_bypass(tmp_path: Path):
    workspace = tmp_path / "workspace"
    allowed = workspace / "allowed"
    outside = workspace / "outside"
    allowed.mkdir(parents=True)
    outside.mkdir()
    policy = BashSandboxPolicy(allowlist=("allowed",))

    with workspace_boundary(workspace):
        result = bash.invoke({"command": "pwd", "workdir": str(outside), "policy": policy})

    assert "outside the Bash allowlist" in result


def test_bash_policy_rejects_network_when_disabled(tmp_path: Path):
    policy = BashSandboxPolicy(network="deny")

    result = bash.invoke(
        {"command": "curl https://example.invalid", "workdir": str(tmp_path), "policy": policy}
    )

    assert "Network access is disabled" in result


def test_bash_timeout_resource_limit_is_enforced(tmp_path: Path):
    result = bash.invoke(
        {
            "command": "python -c 'import time; time.sleep(2)'",
            "workdir": str(tmp_path),
            "timeout": 50,
        }
    )

    assert "Command timed out" in result


def test_bash_preexec_limits_skip_on_windows(monkeypatch):
    monkeypatch.setattr(bash_module.platform, "system", lambda: "Windows")

    assert bash_module._preexec_limits(BashSandboxPolicy(cpu_seconds=1, memory_mb=1)) is None
