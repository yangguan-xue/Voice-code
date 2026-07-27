from __future__ import annotations

import asyncio
from pathlib import Path

from voice_code.tools.bash import BashSandboxPolicy
from voice_code.web_demo.docker_sandbox import (
    DockerSandboxConfig,
    _build_docker_command,
    _container_workdir,
    _quota_error_for_workspace,
    _restore_workspace_baseline,
    _validate_web_demo_command,
    create_web_demo_bash_tool,
)
from voice_code.web_demo.limits import WebDemoLimits
from voice_code.web_demo.sandbox import SandboxManager


def test_container_workdir_maps_workspace_root() -> None:
    workspace = Path("/tmp/demo")

    assert _container_workdir(workspace, workspace) == "/workspace"


def test_container_workdir_maps_nested_directory() -> None:
    workspace = Path("/tmp/demo")

    assert _container_workdir(workspace, workspace / "src" / "pkg") == "/workspace/src/pkg"


def test_build_docker_command_mounts_workspace_and_disables_network() -> None:
    config = DockerSandboxConfig(
        workspace_root=Path("/tmp/demo").resolve(),
        image="voice-code-web-demo-sandbox:latest",
    )

    command = _build_docker_command(
        container_name="demo-box",
        command="python -V",
        workdir=config.workspace_root / "src",
        config=config,
        policy=BashSandboxPolicy(cpu_seconds=7, memory_mb=256),
    )

    assert command[:4] == ["docker", "run", "--rm", "--name"]
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert config.image in command
    mount_spec = command[command.index("-v") + 1]
    assert mount_spec.endswith(":/workspace:rw")
    assert str(config.workspace_root) in mount_spec
    assert command[command.index("-w") + 1] == "/workspace/src"
    assert "cd /workspace/src" in command[-1]
    assert "ulimit -t 7" in command[-1]


def test_validate_web_demo_command_rejects_container_escape_attempts() -> None:
    assert _validate_web_demo_command("cat /etc/passwd") is not None
    assert _validate_web_demo_command("python -c 'import os; print(os.listdir(\"/\"))'") is not None
    assert _validate_web_demo_command("env FOO=1 python -c 'print(1)'") is not None
    assert _validate_web_demo_command("find . -maxdepth 1 && python3 script.py") is not None
    assert _validate_web_demo_command("find ../ -maxdepth 1") is not None
    assert _validate_web_demo_command("ln -s /etc etc_escape") is not None


def test_validate_web_demo_command_allows_workspace_scoped_discovery() -> None:
    assert _validate_web_demo_command("find . -maxdepth 2 -type f") is None
    assert _validate_web_demo_command("rg greeting .") is None


def test_quota_error_for_workspace_detects_file_count_and_size(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"f{index}.txt").write_text("x", encoding="utf-8")

    limits = WebDemoLimits(max_workspace_files=2, max_workspace_bytes=10, max_file_bytes=10)

    assert _quota_error_for_workspace(tmp_path, limits) == "Sandbox file count limit exceeded."


def test_restore_workspace_baseline_removes_untracked_files(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "app.py").write_text("print('ok')\n", encoding="utf-8")

    manager = SandboxManager(tmp_path / "sandboxes", template_repo=template)
    created = asyncio.run(manager.create("demo_restore"))
    (created.path / "extra.txt").write_text("temp\n", encoding="utf-8")
    (created.path / "app.py").write_text("print('changed')\n", encoding="utf-8")

    _restore_workspace_baseline(created.path)

    assert not (created.path / "extra.txt").exists()
    assert (created.path / "app.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_web_demo_bash_tool_allows_discovery_commands_metadata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "voice_code.web_demo.docker_sandbox.docker_sandbox_config",
        lambda workspace_root: DockerSandboxConfig(
            workspace_root=Path(workspace_root).resolve(),
            image="python:3.13-slim",
        ),
    )

    tool = create_web_demo_bash_tool(tmp_path, limits=WebDemoLimits())

    assert tool is not None
    assert tool.metadata["allow_discovery_commands"] is True
