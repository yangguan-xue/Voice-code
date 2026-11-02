#!/usr/bin/env python3
"""Create the desktop app-owned runtime resource layout.

This script intentionally does not download Python or install project dependencies by
default. With `--install`, it bundles a standalone Python distribution and project
dependencies so the packaged runtime does not depend on the build machine's paths.
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
PYTHON="$RUNTIME_DIR/.python/bin/python"
SITE_PACKAGES="$RUNTIME_DIR/site-packages"
if [ ! -x "$PYTHON" ]; then
  echo "Desktop runtime Python not found: $PYTHON" >&2
  echo "Install the bundled runtime before packaging this launcher." >&2
  exit 127
fi
if [ ! -d "$SITE_PACKAGES" ]; then
  echo "Desktop runtime packages not found: $SITE_PACKAGES" >&2
  exit 127
fi
export PYTHONHOME="$RUNTIME_DIR/.python"
export PYTHONPATH="$SITE_PACKAGES${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m voice_code.desktop_runtime.launcher "$@"
"""

WINDOWS_LAUNCHER = """@echo off
setlocal
set "RUNTIME_DIR=%~dp0"
set "PYTHON=%RUNTIME_DIR%.python\\python.exe"
set "SITE_PACKAGES=%RUNTIME_DIR%site-packages"
if not exist "%PYTHON%" (
  echo Desktop runtime Python not found: %PYTHON% 1>&2
  echo Install the bundled runtime before packaging this launcher. 1>&2
  exit /b 127
)
if not exist "%SITE_PACKAGES%" (
  echo Desktop runtime packages not found: %SITE_PACKAGES% 1>&2
  exit /b 127
)
set "PYTHONHOME=%RUNTIME_DIR%.python"
set "PYTHONPATH=%SITE_PACKAGES%;%PYTHONPATH%"
"%PYTHON%" -m voice_code.desktop_runtime.launcher %*
exit /b %ERRORLEVEL%
"""

README = """Voice Code desktop runtime layout

This directory is intended to be bundled as the Tauri app resource `runtime/`.

The layout generator does not download Python, uv, Rust, or project dependencies
unless `--install` is explicitly passed. An installed layout contains a standalone
Python distribution in `.python/` and dependencies in `site-packages/`.

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
    site_packages: Path
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
        "schemaVersion": 2,
        "runtimeMode": "app-owned",
        "status": status,
        "installComplete": install_complete,
        "createdAt": created_at or datetime.now(UTC).isoformat(),
        "createdBy": "tools/build_desktop_runtime.py",
        "python": {
            "posix": ".python/bin/python",
            "windows": ".python\\python.exe",
        },
        "sitePackages": "site-packages",
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


def runtime_python_path(output: Path) -> Path:
    if os.name == "nt":
        return output / ".python" / "python.exe"
    return output / ".python" / "bin" / "python"


def runtime_python_root(output: Path) -> Path:
    return output / ".python"


def runtime_site_packages_path(output: Path) -> Path:
    return output / "site-packages"


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
    return subprocess.run(list(command), check=True, text=True, capture_output=True)


def resolve_runtime_python_source(uv_path: str, python: str) -> Path:
    requested_path = Path(python).expanduser()
    if requested_path.is_file():
        return requested_path.resolve()
    result = run_subprocess(
        (uv_path, "python", "find", "--no-project", "--resolve-links", python)
    )
    source = Path(result.stdout.strip())
    if not source.is_file():
        raise RuntimeError(f"uv returned an invalid Python path: {source}")
    return source.resolve()


def bundle_python_distribution(source_python: Path, target_root: Path) -> None:
    source_root = (
        source_python.parent.parent
        if source_python.parent.name == "bin"
        else source_python.parent
    )
    if not source_root.is_dir():
        raise RuntimeError(f"Python distribution directory was not found: {source_root}")
    shutil.copytree(source_root, target_root, symlinks=True, dirs_exist_ok=True)


def remove_generated_script_wrappers(site_packages: Path) -> None:
    for directory_name in ("bin", "Scripts"):
        directory = site_packages / directory_name
        if directory.is_dir():
            shutil.rmtree(directory)


def remove_build_location_metadata(site_packages: Path) -> None:
    for direct_url in site_packages.glob("*.dist-info/direct_url.json"):
        direct_url.unlink()


def install_runtime_environment(
    output: Path,
    *,
    project_root: Path,
    uv: str | None,
    python: str,
    runner: CommandRunner = run_subprocess,
    python_source: Path | None = None,
) -> RuntimeInstallResult:
    output.mkdir(parents=True, exist_ok=True)
    uv_path = resolve_uv(uv)
    runtime_python = runtime_python_path(output)
    python_root = runtime_python_root(output)
    site_packages = runtime_site_packages_path(output)

    commands: list[tuple[str, ...]] = []
    if not runtime_python.exists():
        source = python_source or resolve_runtime_python_source(uv_path, python)
        bundle_python_distribution(source, python_root)
    site_packages.mkdir(parents=True, exist_ok=True)
    install_command = [
        uv_path,
        "pip",
        "install",
        "--target",
        str(site_packages),
        "--no-editable",
    ]
    if not Path(python).is_file():
        install_command.extend(["--python-version", python])
    install_command.append(str(project_root))
    commands.append(tuple(install_command))
    for command in commands:
        runner(command)
    remove_generated_script_wrappers(site_packages)
    remove_build_location_metadata(site_packages)
    legacy_venv = output / ".venv"
    if legacy_venv.is_dir():
        shutil.rmtree(legacy_venv)

    return RuntimeInstallResult(
        output=output,
        python_root=python_root,
        site_packages=site_packages,
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
            "Also create script launchers that expect an already-installed .python "
            "and site-packages layout. "
            "This still does not download or install dependencies."
        ),
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help=(
            "Explicitly bundle Python with uv and install this project into "
            "site-packages. "
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
        help="Python version or executable to bundle.",
    )
    return parser.parse_args(argv)


def run(
    argv: list[str] | None = None,
    *,
    runner: CommandRunner = run_subprocess,
    python_source: Path | None = None,
) -> int:
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
            python_source=python_source,
        )

    result = write_runtime_layout(
        args.output,
        include_launchers=include_launchers,
        install_complete=install_result is not None,
    )
    print(f"Wrote desktop runtime layout: {result.output}")
    print(f"Manifest: {result.manifest}")
    if install_result is not None:
        print(f"Bundled Python distribution: {install_result.python_root}")
        print(f"Installed packages: {install_result.site_packages}")
        print(f"Runtime Python: {install_result.python}")
    if result.launchers:
        print("Launchers:")
        for launcher in result.launchers:
            print(f"  {launcher}")
    else:
        print("Launchers: skipped; pass --with-launchers after the bundled runtime exists.")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
