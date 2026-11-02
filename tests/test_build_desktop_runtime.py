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


def test_runtime_manifest_records_no_runtime_downloads() -> None:
    manifest = build_desktop_runtime.runtime_manifest(
        include_launchers=False,
        created_at="2026-01-01T00:00:00+00:00",
    )

    assert manifest["schemaVersion"] == 2
    assert manifest["runtimeMode"] == "app-owned"
    assert manifest["status"] == "layout-only"
    assert manifest["installComplete"] is False
    assert manifest["downloadsAtRuntime"] is False
    assert manifest["python"] == {
        "posix": ".python/bin/python",
        "windows": ".python\\python.exe",
    }
    assert manifest["sitePackages"] == "site-packages"
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


def test_launchers_are_written_without_creating_runtime(tmp_path: Path) -> None:
    result = build_desktop_runtime.write_runtime_layout(tmp_path, include_launchers=True)

    assert {path.name for path in result.launchers} == {
        "voice-code-agent",
        "voice-code-agent.cmd",
    }
    assert not (tmp_path / ".venv").exists()

    posix_launcher = tmp_path / "voice-code-agent"
    windows_launcher = tmp_path / "voice-code-agent.cmd"
    assert os.access(posix_launcher, os.X_OK)
    assert "voice_code.desktop_runtime.launcher" in posix_launcher.read_text(encoding="utf-8")
    assert "PYTHONHOME" in posix_launcher.read_text(encoding="utf-8")
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
    python_source = _fake_python_source(tmp_path)

    result = build_desktop_runtime.install_runtime_environment(
        output,
        project_root=project_root,
        uv="uv-bin",
        python="3.12",
        runner=lambda command: commands.append(tuple(command)),
        python_source=python_source,
    )

    assert commands == [
        (
            "uv-bin",
            "pip",
            "install",
            "--target",
            str(output / "site-packages"),
            "--no-editable",
            "--python-version",
            "3.12",
            str(project_root),
        ),
    ]
    assert result.output == output
    assert result.python_root == output / ".python"
    assert result.site_packages == output / "site-packages"
    assert result.python == build_desktop_runtime.runtime_python_path(output)
    assert result.python.is_file()
    assert not (output / "site-packages" / "bin").exists()


def test_install_runtime_environment_reuses_existing_bundled_python(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []
    output = tmp_path / "runtime"
    project_root = tmp_path / "project"
    project_root.mkdir()
    runtime_python = build_desktop_runtime.runtime_python_path(output)
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
            "--target",
            str(output / "site-packages"),
            "--no-editable",
            "--python-version",
            "3.12",
            str(project_root),
        )
    ]
    assert result.python_root == output / ".python"
    assert result.python == runtime_python


def test_install_runtime_environment_removes_build_location_metadata(tmp_path: Path) -> None:
    output = tmp_path / "runtime"
    project_root = tmp_path / "project"
    project_root.mkdir()
    direct_url = output / "site-packages" / "voice_code-0.2.0.dist-info" / "direct_url.json"
    direct_url.parent.mkdir(parents=True)
    direct_url.write_text('{"url":"file:///build/workspace"}', encoding="utf-8")

    build_desktop_runtime.install_runtime_environment(
        output,
        project_root=project_root,
        uv="uv-bin",
        python="3.12",
        runner=lambda _command: None,
        python_source=_fake_python_source(tmp_path),
    )

    assert not direct_url.exists()


def test_run_install_writes_installed_layout_after_commands(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []
    output = tmp_path / "runtime"
    project_root = tmp_path / "project"
    project_root.mkdir()
    python_source = _fake_python_source(tmp_path)

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
        runner=lambda command: commands.append(tuple(command)),
        python_source=python_source,
    )

    assert exit_code == 0
    assert len(commands) == 1
    assert (output / "voice-code-agent").is_file()
    assert (output / "voice-code-agent.cmd").is_file()

    manifest = json.loads((output / "runtime-manifest.json").read_text(encoding="utf-8"))
    assert manifest["python"]["posix"] == ".python/bin/python"
    assert manifest["status"] == "installed"
    assert manifest["installComplete"] is True


def _fake_python_source(tmp_path: Path) -> Path:
    source_root = tmp_path / "python-source"
    source_bin = source_root / "bin"
    source_bin.mkdir(parents=True)
    source_python = source_bin / "python"
    source_python.write_text("python", encoding="utf-8")
    (source_root / "lib").mkdir()
    return source_python
