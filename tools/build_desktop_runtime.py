#!/usr/bin/env python3
"""Create the desktop app-owned runtime resource layout.

This script creates the deterministic files that Tauri packaging can include. By
default it does not download Python or install project dependencies; passing
`--install` explicitly prepares an app-owned managed Python runtime for release
packaging.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENTRYPOINTS = (
    "reasoning-desktop-bridge",
    "reasoning-desktop-voice-bridge",
    "voice-code-desktop-metadata",
)

POSIX_LAUNCHER_NAME = "voice-code-agent"
WINDOWS_LAUNCHER_NAME = "voice-code-agent.cmd"
MANIFEST_NAME = "runtime-manifest.json"
README_NAME = "README.txt"

POSIX_LAUNCHER = """#!/usr/bin/env sh
set -eu
RUNTIME_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON=""
for candidate in \
  "$RUNTIME_DIR"/python/*/bin/python \
  "$RUNTIME_DIR"/python/*/bin/python3 \
  "$RUNTIME_DIR"/.venv/bin/python; do
  if [ -x "$candidate" ]; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Desktop runtime Python not found under $RUNTIME_DIR/python or $RUNTIME_DIR/.venv" >&2
  echo "Create the runtime environment before packaging this launcher." >&2
  exit 127
fi
exec "$PYTHON" -m voice_code.desktop_runtime.launcher "$@"
"""

WINDOWS_LAUNCHER = """@echo off
setlocal
set "RUNTIME_DIR=%~dp0"
set "PYTHON="
for /d %%D in ("%RUNTIME_DIR%python\\*") do (
  if exist "%%~fD\\python.exe" (
    set "PYTHON=%%~fD\\python.exe"
    goto :found_python
  )
)
if exist "%RUNTIME_DIR%.venv\\Scripts\\python.exe" (
  set "PYTHON=%RUNTIME_DIR%.venv\\Scripts\\python.exe"
)
:found_python
if not defined PYTHON (
  echo Desktop runtime Python not found under %RUNTIME_DIR%python or %RUNTIME_DIR%.venv 1>&2
  echo Create the runtime environment before packaging this launcher. 1>&2
  exit /b 127
)
"%PYTHON%" -m voice_code.desktop_runtime.launcher %*
exit /b %ERRORLEVEL%
"""

README = """Voice Code desktop runtime layout

This directory is intended to be bundled as the Tauri app resource `runtime/`.

The default layout generator does not download Python, uv, Rust, or project
dependencies. Release packaging may explicitly prepare an app-owned managed
Python under `python/` with the `voice-code` package installed.

Expected launcher contract:
  voice-code-agent reasoning-desktop-bridge --port 0 --workspace <path>
  voice-code-agent reasoning-desktop-voice-bridge --port 0 --workspace <path>
  voice-code-agent voice-code-desktop-metadata --workspace <path>
"""


@dataclass(frozen=True)
class RuntimeLayoutResult:
    output: Path
    manifest: Path
    launchers: tuple[Path, ...]


@dataclass(frozen=True)
class RuntimeInstallResult:
    output: Path
    python_root: Path
    python: Path
    commands: tuple[tuple[str, ...], ...]


CommandRunner = Callable[[Sequence[str]], object]


def runtime_manifest(
    *,
    include_launchers: bool,
    install_complete: bool = False,
    created_at: str | None = None,
) -> dict[str, Any]:
    if install_complete:
        status = "installed"
    elif include_launchers:
        status = "launcher-layout"
    else:
        status = "layout-only"

    return {
        "schemaVersion": 1,
        "runtimeMode": "app-owned",
        "status": status,
        "installComplete": install_complete,
        "createdAt": created_at or datetime.now(UTC).isoformat(),
        "createdBy": "tools/build_desktop_runtime.py",
        "python": {
            "posix": "python/*/bin/python",
            "windows": "python\\*\\python.exe",
            "fallbackPosix": ".venv/bin/python",
            "fallbackWindows": ".venv\\Scripts\\python.exe",
        },
        "launchers": {
            "posix": POSIX_LAUNCHER_NAME if include_launchers else None,
            "windows": WINDOWS_LAUNCHER_NAME if include_launchers else None,
            "windowsFrozenPreferred": "voice-code-agent.exe",
        },
        "entrypoints": list(ENTRYPOINTS),
        "downloadsAtRuntime": False,
    }


def write_runtime_layout(
    output: Path,
    *,
    include_launchers: bool,
    install_complete: bool = False,
) -> RuntimeLayoutResult:
    output.mkdir(parents=True, exist_ok=True)

    readme = output / README_NAME
    readme.write_text(README, encoding="utf-8")

    manifest_path = output / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(
            runtime_manifest(
                include_launchers=include_launchers,
                install_complete=install_complete,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    launchers: list[Path] = []
    if include_launchers:
        posix_launcher = output / POSIX_LAUNCHER_NAME
        posix_launcher.write_text(POSIX_LAUNCHER, encoding="utf-8")
        posix_launcher.chmod(
            posix_launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        launchers.append(posix_launcher)

        windows_launcher = output / WINDOWS_LAUNCHER_NAME
        windows_launcher.write_text(WINDOWS_LAUNCHER, encoding="utf-8", newline="\r\n")
        launchers.append(windows_launcher)

    return RuntimeLayoutResult(
        output=output,
        manifest=manifest_path,
        launchers=tuple(launchers),
    )


def runtime_python_root(output: Path) -> Path:
    return output / "python"


def find_runtime_python(output: Path) -> Path | None:
    if os.name == "nt":
        candidates = sorted(runtime_python_root(output).glob("*/python.exe"))
    else:
        candidates = sorted(runtime_python_root(output).glob("*/bin/python"))
        candidates.extend(sorted(runtime_python_root(output).glob("*/bin/python3")))
    return candidates[0] if candidates else None


def runtime_python_path(output: Path) -> Path:
    runtime_python = find_runtime_python(output)
    if runtime_python is None:
        raise RuntimeError(
            f"Managed desktop runtime Python was not found under {runtime_python_root(output)}."
        )
    return runtime_python


def resolve_uv(uv: str | None) -> str:
    if uv:
        return uv
    discovered = shutil.which("uv")
    if discovered:
        return discovered
    raise RuntimeError(
        "uv was not found. Install uv or pass --uv /path/to/uv before using --install."
    )


def run_subprocess(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), check=True, text=True)


def install_runtime_environment(
    output: Path,
    *,
    project_root: Path,
    uv: str | None,
    python: str,
    runner: CommandRunner = run_subprocess,
) -> RuntimeInstallResult:
    output.mkdir(parents=True, exist_ok=True)
    uv_path = resolve_uv(uv)
    python_root = runtime_python_root(output)
    runtime_python = find_runtime_python(output)

    commands: list[tuple[str, ...]] = []
    if runtime_python is None:
        command = (
            uv_path,
            "python",
            "install",
            python,
            "--install-dir",
            str(python_root),
            "--no-bin",
            "--no-registry",
        )
        commands.append(command)
        runner(command)
        runtime_python = find_runtime_python(output)
        if runtime_python is None:
            raise RuntimeError(
                f"uv completed but no managed Python was found under {python_root}."
            )

    command = (
        uv_path,
        "pip",
        "install",
        "--system",
        "--break-system-packages",
        "--python",
        str(runtime_python),
        str(project_root),
    )
    commands.append(command)
    runner(command)

    return RuntimeInstallResult(
        output=output,
        python_root=python_root,
        python=runtime_python,
        commands=tuple(commands),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=(
            "Runtime resource directory to create, for example "
            "desktop/src-tauri/resources/runtime"
        ),
    )
    parser.add_argument(
        "--with-launchers",
        action="store_true",
        help=(
            "Also create script launchers that expect an already-installed runtime. "
            "This still does not download or install dependencies."
        ),
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help=(
            "Explicitly create a managed Python runtime with uv and install this project. "
            "This may download Python packages via uv; it is never enabled by default."
        ),
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root to install when --install is enabled.",
    )
    parser.add_argument(
        "--uv",
        help="Path to uv. Defaults to uv on PATH when --install is enabled.",
    )
    parser.add_argument(
        "--python",
        default="3.12",
        help="Python version passed to `uv python install`.",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None, *, runner: CommandRunner = run_subprocess) -> int:
    args = parse_args(argv)
    include_launchers = args.with_launchers or args.install
    install_result: RuntimeInstallResult | None = None

    if args.install:
        install_result = install_runtime_environment(
            args.output,
            project_root=args.project_root,
            uv=args.uv,
            python=args.python,
            runner=runner,
        )

    result = write_runtime_layout(
        args.output,
        include_launchers=include_launchers,
        install_complete=install_result is not None,
    )
    print(f"Wrote desktop runtime layout: {result.output}")
    print(f"Manifest: {result.manifest}")
    if install_result is not None:
        print(f"Installed runtime environment: {install_result.python_root}")
        print(f"Runtime Python: {install_result.python}")
    if result.launchers:
        print("Launchers:")
        for launcher in result.launchers:
            print(f"  {launcher}")
    else:
        print("Launchers: skipped; pass --with-launchers after the runtime exists.")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
