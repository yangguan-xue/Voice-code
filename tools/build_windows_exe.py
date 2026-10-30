#!/usr/bin/env python3
"""Build the frozen Windows desktop runtime launcher."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "src" / "voice_code" / "desktop_runtime" / "launcher.py"
DEFAULT_OUTPUT = ROOT / "dist" / "windows"

HIDDEN_IMPORTS = (
    "voice_code.desktop.cli",
    "voice_code.desktop_voice.cli",
    "voice_code.desktop.metadata",
)


def build_command(output: Path, work_root: Path, *, name: str = "voice-code-agent") -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        name,
        "--distpath",
        str(output),
        "--workpath",
        str(work_root / "work"),
        "--specpath",
        str(work_root / "spec"),
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        f"{ROOT / 'models.toml'}{os.pathsep}.",
    ]
    for module in HIDDEN_IMPORTS:
        command.extend(["--hidden-import", module])
    command.append(str(LAUNCHER))
    return command


def build_exe(output: Path = DEFAULT_OUTPUT, *, name: str = "voice-code-agent") -> Path:
    if os.name != "nt":
        raise RuntimeError("Windows exe builds must run on Windows.")
    if not LAUNCHER.is_file():
        raise FileNotFoundError(f"Desktop runtime launcher not found: {LAUNCHER}")

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="voice-code-pyinstaller-") as temporary:
        command = build_command(output, Path(temporary), name=name)
        subprocess.run(command, cwd=ROOT, check=True)

    executable = output / f"{name}.exe"
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller completed without creating {executable}")
    return executable


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--name", default="voice-code-agent")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    executable = build_exe(args.output, name=args.name)
    print(f"Built Windows executable: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
