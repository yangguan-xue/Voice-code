from __future__ import annotations

from pathlib import Path

from voice_code.platform_fs import best_effort_private_permissions


def test_best_effort_private_permissions_ignores_chmod_failure(monkeypatch) -> None:
    def fail_chmod(self: Path, _mode: int) -> None:
        raise OSError("chmod unsupported")

    monkeypatch.setattr(Path, "chmod", fail_chmod)

    best_effort_private_permissions(Path("state.json"))
    best_effort_private_permissions(Path("state-dir"), directory=True)
