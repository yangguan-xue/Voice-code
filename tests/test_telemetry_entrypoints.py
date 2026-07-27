from __future__ import annotations

import io
import sys
from pathlib import Path

from voice_code import cli, tui, voice_cli
from voice_code.memory import cli as memory_cli


def test_primary_cli_forwards_logging_options_to_tui(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_setup_logging", lambda debug, log_format: None)
    monkeypatch.setattr(cli, "tui_main", lambda argv: captured.update(argv=argv))

    cli.main(["--debug", "--log-format", "json"])

    assert captured["argv"] == ["--debug", "--log-format", "json"]


def test_voice_cli_accepts_shared_logging_options() -> None:
    args = voice_cli.parse_args(["--debug", "--log-format", "json"])

    assert args.debug is True
    assert args.log_format == "json"


def test_direct_tui_accepts_shared_logging_options() -> None:
    args = tui.parse_args(["--debug", "--log-format", "json"])

    assert args.debug is True
    assert args.log_format == "json"


def test_direct_tui_routes_logging_to_file_stream(monkeypatch) -> None:
    configured: dict[str, object] = {}
    stream = io.StringIO()
    ran: list[bool] = []

    class DummyApp:
        def __init__(self, *, profile) -> None:
            assert profile == "demo"

        def run(self) -> None:
            ran.append(True)

    monkeypatch.setattr(tui, "_open_tui_log_stream", lambda: stream)
    monkeypatch.setattr(tui, "configure_logging", lambda **kwargs: configured.update(kwargs))
    monkeypatch.setattr(tui, "AgentApp", DummyApp)

    tui.main(["--profile", "demo"])

    assert ran == [True]
    assert configured == {"debug": False, "json_output": None, "stream": stream}
    assert stream.closed is True


def test_tui_log_stream_uses_reasoning_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REASONING_HOME", str(tmp_path))

    stream = tui._open_tui_log_stream()
    try:
        assert Path(stream.name) == tmp_path / "logs" / "reasoning-tui.log"
    finally:
        stream.close()


def test_memory_cli_initializes_shared_logging(monkeypatch) -> None:
    configured: dict[str, object] = {}
    monkeypatch.setattr(
        memory_cli,
        "configure_logging",
        lambda **kwargs: configured.update(kwargs),
    )
    monkeypatch.setattr(memory_cli, "print_paths", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["reasoning-memory", "paths", "--debug", "--log-format", "json"],
    )

    memory_cli.main()

    assert configured == {"debug": True, "json_output": True}
