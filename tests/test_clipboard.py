from __future__ import annotations

import subprocess

from voice_code import clipboard


def test_copy_to_clipboard_uses_clip_exe_on_windows(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)

    assert clipboard.copy_to_clipboard("hello") is True
    assert calls == [
        {
            "cmd": ["clip.exe"],
            "input": "hello",
            "text": True,
            "check": True,
            "timeout": 2,
        }
    ]


def test_copy_to_clipboard_returns_false_when_windows_clip_fails(monkeypatch) -> None:
    def fail_run(*_args, **_kwargs):
        raise FileNotFoundError("clip.exe")

    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    monkeypatch.setattr(clipboard.subprocess, "run", fail_run)

    assert clipboard.copy_to_clipboard("hello") is False
