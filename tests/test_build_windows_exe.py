from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "build_windows_exe.py"
SPEC = importlib.util.spec_from_file_location("build_windows_exe", SCRIPT_PATH)
assert SPEC is not None
build_windows_exe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = build_windows_exe
SPEC.loader.exec_module(build_windows_exe)


def test_build_command_targets_frozen_desktop_launcher(tmp_path: Path) -> None:
    command = build_windows_exe.build_command(tmp_path, tmp_path / "work")

    assert command[:7] == [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
    ]
    assert "voice_code.desktop_voice.cli" in command
    assert "voice_code.desktop.metadata" in command
    assert str(build_windows_exe.LAUNCHER) == command[-1]
    assert str(tmp_path) in command


def test_default_output_is_windows_dist_directory() -> None:
    args = build_windows_exe.parse_args([])

    assert args.output == build_windows_exe.DEFAULT_OUTPUT
    assert args.name == "voice-code-agent"
