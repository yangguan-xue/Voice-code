from __future__ import annotations

import io
import sys
from pathlib import Path

from voice_code import cli, tui, voice_cli
from voice_code.memory import cli as memory_cli
from voice_code.permissions import PermissionBehavior, can_use_tool


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


def test_voice_cli_defaults_to_local_stt_tts_urls() -> None:
    args = voice_cli.parse_args([])

    assert args.stt_url == "http://localhost:8765"
    assert args.tts_url == "http://localhost:8775"


def test_voice_cli_does_not_auto_enable_stepfun_from_env(monkeypatch) -> None:
    monkeypatch.setenv("STEPFUN_API_KEY", "test-key")
    args = voice_cli.parse_args([])

    assert voice_cli._should_use_stepfun(args) is False


def test_voice_cli_can_explicitly_enable_stepfun_from_env(monkeypatch) -> None:
    monkeypatch.setenv("STEPFUN_API_KEY", "test-key")
    args = voice_cli.parse_args(["--use-stepfun"])

    assert voice_cli._should_use_stepfun(args) is True
    assert voice_cli._resolve_stepfun_key(args) == "test-key"


def test_voice_permission_context_does_not_fall_back_to_stdin() -> None:
    ctx = voice_cli._make_voice_permission_context()

    write_decision = can_use_tool(
        tool_name="write",
        tool_input={"file_path": "demo.py", "content": "print('hi')"},
        tool_metadata={"is_readonly": False},
        context=ctx,
    )
    bash_decision = can_use_tool(
        tool_name="bash",
        tool_input={"command": "echo hi > demo.txt"},
        tool_metadata={"is_readonly": False},
        context=ctx,
    )

    assert write_decision.behavior == PermissionBehavior.ALLOW
    assert bash_decision.behavior == PermissionBehavior.DENY
    assert "语音模式" in bash_decision.message


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
