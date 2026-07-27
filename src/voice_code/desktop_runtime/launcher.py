"""Dispatch packaged desktop runtime entry points.

The Tauri app-owned runtime invokes one launcher binary with the desired Python
entry point as argv[1]. Keeping this dispatcher tiny lets packaging decide
whether the launcher is a console script, a bundled Python executable, or a
frozen wrapper without changing the Tauri bridge contract.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

Entrypoint = Callable[[Sequence[str]], object]

_ENTRYPOINTS: dict[str, tuple[str, str]] = {
    "reasoning-desktop-bridge": ("voice_code.desktop.cli", "main"),
    "reasoning-desktop-voice-bridge": ("voice_code.desktop_voice.cli", "main"),
    "voice-code-desktop-metadata": ("voice_code.desktop.metadata", "main"),
    "reasoning-desktop-metadata": ("voice_code.desktop.metadata", "main"),
}


def resolve_entrypoint(name: str) -> Entrypoint:
    """Return the Python callable for a packaged desktop runtime entry point."""
    try:
        module_name, function_name = _ENTRYPOINTS[name]
    except KeyError as exc:
        raise KeyError(name) from exc
    module = importlib.import_module(module_name)
    entrypoint = getattr(module, function_name)
    if not callable(entrypoint):  # pragma: no cover - defensive
        raise TypeError(f"{module_name}:{function_name} is not callable")
    return entrypoint


def dispatch(
    argv: Sequence[str],
    *,
    resolver: Callable[[str], Entrypoint | None] = resolve_entrypoint,
    stderr: TextIO = sys.stderr,
) -> int:
    """Dispatch argv to the selected desktop runtime entry point."""
    if not argv:
        _print_usage("Missing desktop runtime entry point.", stderr)
        return 2

    entrypoint_name = argv[0]
    try:
        entrypoint = resolver(entrypoint_name)
    except KeyError:
        entrypoint = None
    if entrypoint is None:
        _print_usage(f"Unknown desktop runtime entry point: {entrypoint_name}", stderr)
        return 2

    result = entrypoint(list(argv[1:]))
    return result if isinstance(result, int) else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Console script entry point for `voice-code-agent`."""
    return dispatch(list(sys.argv[1:] if argv is None else argv))


def _print_usage(message: str, stderr: TextIO) -> None:
    available = ", ".join(sorted(_ENTRYPOINTS))
    print(message, file=stderr)
    print(f"Available entry points: {available}", file=stderr)


if __name__ == "__main__":
    raise SystemExit(main())
