from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from voice_code.integrations.mcp import (
    MCPServerConfig,
    _preexec_limits,
    load_mcp_tools,
    trust_mcp_server,
)

try:
    import resource
except ImportError:  # pragma: no cover - Windows test collection
    resource = None  # type: ignore[assignment]

_SERVER = """\
import json
import os
import sys

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2025-03-26", "capabilities": {}}
    elif method == "tools/list":
        leaked = os.getenv("SHOULD_NOT_LEAK", "")
        result = {
            "tools": [
                {"name": "env", "description": leaked, "inputSchema": {"type": "object"}}
            ]
        }
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": os.getenv("SHOULD_NOT_LEAK", "")}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
"""


def _write_config(
    tmp_path: Path,
    server: Path,
    *,
    env: dict[str, str] | None = None,
    command_allowlist: list[str] | None = None,
    resources: dict[str, int] | None = None,
) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fake": {
                        "command": sys.executable,
                        "args": [str(server)],
                        "env": env or {},
                        "env_allowlist": list((env or {}).keys()),
                        "command_allowlist": command_allowlist or [sys.executable],
                    }
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_untrusted_mcp_server_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "fake_mcp.py"
    server.write_text(_SERVER, encoding="utf-8")
    _write_config(tmp_path, server)
    monkeypatch.setenv("REASONING_ENABLE_MCP", "1")

    assert await load_mcp_tools(tmp_path) == []


@pytest.mark.asyncio
async def test_mcp_does_not_inherit_host_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "fake_mcp.py"
    server.write_text(_SERVER, encoding="utf-8")
    _write_config(tmp_path, server)
    trust_mcp_server(tmp_path, "fake")
    monkeypatch.setenv("REASONING_ENABLE_MCP", "1")
    monkeypatch.setenv("SHOULD_NOT_LEAK", "top-secret")

    tools = await load_mcp_tools(tmp_path)

    assert [tool.name for tool in tools] == ["mcp__fake__env"]
    assert "top-secret" not in tools[0].description
    assert await tools[0].ainvoke({}) == ""


@pytest.mark.asyncio
async def test_mcp_env_allowlist_passes_only_declared_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "fake_mcp.py"
    server.write_text(_SERVER, encoding="utf-8")
    _write_config(tmp_path, server, env={"SHOULD_NOT_LEAK": "allowed-value"})
    trust_mcp_server(tmp_path, "fake")
    monkeypatch.setenv("REASONING_ENABLE_MCP", "1")
    monkeypatch.setenv("SHOULD_NOT_LEAK", "host-secret")

    tools = await load_mcp_tools(tmp_path)

    assert await tools[0].ainvoke({}) == "allowed-value"


@pytest.mark.asyncio
async def test_mcp_inline_trusted_flag_does_not_bypass_first_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "fake_mcp.py"
    server.write_text(_SERVER, encoding="utf-8")
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fake": {
                        "command": sys.executable,
                        "args": [str(server)],
                        "trusted": True,
                        "command_allowlist": [sys.executable],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REASONING_ENABLE_MCP", "1")

    assert await load_mcp_tools(tmp_path) == []


@pytest.mark.asyncio
async def test_mcp_command_allowlist_rejects_unlisted_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "fake_mcp.py"
    server.write_text(_SERVER, encoding="utf-8")
    _write_config(tmp_path, server, command_allowlist=["/bin/false"])
    trust_mcp_server(tmp_path, "fake")
    monkeypatch.setenv("REASONING_ENABLE_MCP", "1")

    assert await load_mcp_tools(tmp_path) == []


def test_mcp_preexec_limits_apply_cpu_and_memory(monkeypatch):
    if resource is None:
        pytest.skip("resource module is POSIX-only")
    calls = []
    config = MCPServerConfig(
        name="fake",
        command="python",
        args=("fake_mcp.py",),
        env={},
        cwd=Path("/tmp"),
        command_allowlist=("python",),
        env_allowlist=(),
        timeout_seconds=20,
        cpu_seconds=3,
        memory_mb=32,
        trusted=True,
    )

    monkeypatch.setattr("voice_code.integrations.mcp.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "voice_code.integrations.mcp.resource.setrlimit",
        lambda limit, values: calls.append((limit, values)),
    )

    apply_limits = _preexec_limits(config)
    assert apply_limits is not None

    apply_limits()

    assert calls == [
        (resource.RLIMIT_CPU, (3, 3)),
        (resource.RLIMIT_AS, (32 * 1024 * 1024, 32 * 1024 * 1024)),
    ]


def test_mcp_preexec_limits_skip_on_windows(monkeypatch):
    config = MCPServerConfig(
        name="fake",
        command="python",
        args=("fake_mcp.py",),
        env={},
        cwd=Path("/tmp"),
        command_allowlist=("python",),
        env_allowlist=(),
        timeout_seconds=20,
        cpu_seconds=3,
        memory_mb=32,
        trusted=True,
    )

    monkeypatch.setattr("voice_code.integrations.mcp.platform.system", lambda: "Windows")

    assert _preexec_limits(config) is None
