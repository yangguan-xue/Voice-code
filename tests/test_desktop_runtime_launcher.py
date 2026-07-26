from __future__ import annotations

from collections.abc import Sequence
from io import StringIO

from voice_code.desktop_runtime.launcher import (
    dispatch,
    resolve_entrypoint,
)


def test_dispatch_calls_requested_entrypoint_with_remaining_args() -> None:
    calls: list[tuple[str, list[str]]] = []

    def resolver(name: str):
        def entrypoint(argv: Sequence[str]) -> int:
            calls.append((name, list(argv)))
            return 7

        return entrypoint

    status = dispatch(
        ["reasoning-desktop-bridge", "--port", "0", "--workspace", "/tmp/project"],
        resolver=resolver,
        stderr=StringIO(),
    )

    assert status == 7
    assert calls == [
        (
            "reasoning-desktop-bridge",
            ["--port", "0", "--workspace", "/tmp/project"],
        )
    ]


def test_dispatch_returns_usage_error_for_unknown_entrypoint() -> None:
    stderr = StringIO()

    status = dispatch(["unknown-entrypoint"], resolver=lambda _name: None, stderr=stderr)

    assert status == 2
    assert "Unknown desktop runtime entry point: unknown-entrypoint" in stderr.getvalue()
    assert "reasoning-desktop-bridge" in stderr.getvalue()


def test_resolve_entrypoint_accepts_metadata_contract_name() -> None:
    entrypoint = resolve_entrypoint("voice-code-desktop-metadata")

    assert callable(entrypoint)


def test_resolve_entrypoint_accepts_legacy_metadata_name() -> None:
    entrypoint = resolve_entrypoint("reasoning-desktop-metadata")

    assert callable(entrypoint)
