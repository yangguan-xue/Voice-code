from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "build_desktop_runtime.py"
SPEC = importlib.util.spec_from_file_location("build_desktop_runtime", SCRIPT_PATH)
assert SPEC is not None
build_desktop_runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = build_desktop_runtime
SPEC.loader.exec_module(build_desktop_runtime)


def _managed_python(output: Path) -> Path:
    if os.name == "nt":
        return output / "python" / "cpython-test" / "python.exe"
    return output / "python" / "cpython-test" / "bin" / "python"


def test_runtime_manifest_records_no_runtime_downloads() -> None:
    manifest = build_desktop_runtime.runtime_manifest(
        include_launchers=False,
        created_at="2026-01-01T00:00:00+00:00",
    )

    assert manifest["runtimeMode"] == "app-owned"
    assert manifest["status"] == "layout-only"
    assert manifest["installComplete"] is False
    assert manifest["downloadsAtRuntime"] is False
    assert manifest["launchers"]["posix"] is None
    assert manifest["launchers"]["windows"] is None
    assert manifest["entrypoints"] == [
        "reasoning-desktop-bridge",
        "reasoning-desktop-voice-bridge",
        "voice-code-desktop-metadata",
    ]


def test_layout_only_does_not_create_launcher(tmp_path: Path) -> None:
    result = build_desktop_runtime.write_runtime_layout(tmp_path, include_launchers=False)

    assert result.output == tmp_path
    assert result.launchers == ()
    assert result.manifest.is_file()
    assert (tmp_path / "README.txt").is_file()
    assert not (tmp_path / "voice-code-agent").exists()
    assert not (tmp_path / "voice-code-agent.cmd").exists()

    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["status"] == "layout-only"


def test_launchers_are_written_without_creating_venv(tmp_path: Path) -> None:
    result = build_desktop_runtime.write_runtime_layout(tmp_path, include_launchers=True)

    assert {path.name for path in result.launchers} == {
        "voice-code-agent",
        "voice-code-agent.cmd",
    }
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / "python").exists()

    posix_launcher = tmp_path / "voice-code-agent"
    windows_launcher = tmp_path / "voice-code-agent.cmd"
    assert os.access(posix_launcher, os.X_OK)
    assert "voice_code.desktop_runtime.launcher" in posix_launcher.read_text(encoding="utf-8")
    assert "voice_code.desktop_runtime.launcher" in windows_launcher.read_text(
        encoding="utf-8"
    )

    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["status"] == "launcher-layout"
    assert manifest["installComplete"] is False
    assert manifest["launchers"]["posix"] == "voice-code-agent"
    assert manifest["launchers"]["windows"] == "voice-code-agent.cmd"
    assert manifest["launchers"]["windowsFrozenPreferred"] == "voice-code-agent.exe"


def test_install_runtime_environment_runs_explicit_uv_commands(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []
    output = tmp_path / "runtime"
    project_root = tmp_path / "project"
    project_root.mkdir()
    runtime_python = _managed_python(output)

    def fake_runner(command: tuple[str, ...]) -> None:
        commands.append(tuple(command))
        if command[:3] == ("uv-bin", "python", "install"):
            runtime_python.parent.mkdir(parents=True)
            runtime_python.touch()

    result = build_desktop_runtime.install_runtime_environment(
        output,
        project_root=project_root,
        uv="uv-bin",
        python="3.12",
        runner=fake_runner,
    )

    assert commands == [
        (
            "uv-bin",
            "python",
            "install",
            "3.12",
            "--install-dir",
            str(output / "python"),
            "--no-bin",
            "--no-registry",
        ),
        (
            "uv-bin",
            "pip",
            "install",
            "--system",
            "--break-system-packages",
            "--python",
            str(runtime_python),
            str(project_root),
        ),
    ]
    assert result.output == output
    assert result.python_root == output / "python"
    assert result.python == runtime_python


def test_install_runtime_environment_reuses_existing_managed_python(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []
    output = tmp_path / "runtime"
    project_root = tmp_path / "project"
    project_root.mkdir()
    runtime_python = _managed_python(output)
    runtime_python.parent.mkdir(parents=True)
    runtime_python.touch()

    result = build_desktop_runtime.install_runtime_environment(
        output,
        project_root=project_root,
        uv="uv-bin",
        python="3.12",
        runner=lambda command: commands.append(tuple(command)),
    )

    assert commands == [
        (
            "uv-bin",
            "pip",
            "install",
            "--system",
            "--break-system-packages",
            "--python",
            str(runtime_python),
            str(project_root),
        )
    ]
    assert result.python == runtime_python


def test_install_runtime_environment_fails_if_uv_does_not_install_python(
    tmp_path: Path,
) -> None:
    output = tmp_path / "runtime"
    project_root = tmp_path / "project"
    project_root.mkdir()

    try:
        build_desktop_runtime.install_runtime_environment(
            output,
            project_root=project_root,
            uv="uv-bin",
            python="3.12",
            runner=lambda command: None,
        )
    except RuntimeError as exc:
        assert "no managed Python was found" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_run_install_writes_installed_layout_after_commands(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []
    output = tmp_path / "runtime"
    project_root = tmp_path / "project"
    project_root.mkdir()
    runtime_python = _managed_python(output)

    def fake_runner(command: tuple[str, ...]) -> None:
        commands.append(tuple(command))
        if command[:3] == ("uv-bin", "python", "install"):
            runtime_python.parent.mkdir(parents=True)
            runtime_python.touch()

    exit_code = build_desktop_runtime.run(
        [
            "--output",
            str(output),
            "--install",
            "--project-root",
            str(project_root),
            "--uv",
            "uv-bin",
        ],
        runner=fake_runner,
    )

    assert exit_code == 0
    assert len(commands) == 2
    assert (output / "voice-code-agent").is_file()
    assert (output / "voice-code-agent.cmd").is_file()

    manifest = json.loads((output / "runtime-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "installed"
    assert manifest["installComplete"] is True
