"""Minimal stdio MCP client and LangChain tool adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import ConfigDict, Field, create_model

from voice_code import __version__
from voice_code.audit import record_audit_event

try:
    import resource
except ImportError:  # pragma: no cover - Windows import smoke
    resource = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
_PROTOCOL_VERSION = "2025-03-26"
_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_TRUST_FILE = ".mcp.trust.json"


def client_info() -> dict[str, str]:
    return {"name": "voice-code", "version": __version__}


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: tuple[str, ...]
    env: dict[str, str]
    cwd: Path
    command_allowlist: tuple[str, ...]
    env_allowlist: tuple[str, ...]
    timeout_seconds: int
    cpu_seconds: int | None
    memory_mb: int | None
    trusted: bool


class MCPProtocolError(RuntimeError):
    pass


def _trust_path(workspace: Path) -> Path:
    return workspace / _TRUST_FILE


def _load_trusted_servers(workspace: Path) -> set[str]:
    try:
        payload = json.loads(_trust_path(workspace).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    servers = payload.get("servers", [])
    if not isinstance(servers, list):
        return set()
    return {str(server) for server in servers if isinstance(server, str)}


def trust_mcp_server(workspace: str | Path, name: str) -> None:
    root = Path(workspace).resolve()
    trusted = sorted(_load_trusted_servers(root) | {name})
    _trust_path(root).write_text(json.dumps({"servers": trusted}, sort_keys=True), encoding="utf-8")


def _load_configs(workspace: Path) -> list[MCPServerConfig]:
    config_path = workspace / ".mcp.json"
    if not config_path.is_file():
        for parent in workspace.parents:
            candidate = parent / ".mcp.json"
            if candidate.is_file():
                config_path = candidate
                break
            if (parent / ".git").exists():
                break
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_servers = payload.get("mcpServers", payload.get("servers", {}))
    if not isinstance(raw_servers, dict):
        return []
    trusted_servers = _load_trusted_servers(config_path.parent)
    configs: list[MCPServerConfig] = []
    for name, raw in raw_servers.items():
        if not isinstance(raw, dict) or raw.get("disabled") is True:
            continue
        command = raw.get("command")
        args = raw.get("args", [])
        env = raw.get("env", {})
        command_allowlist = raw.get("command_allowlist", [])
        env_allowlist = raw.get("env_allowlist", [])
        resources = raw.get("resources", {})
        if not isinstance(command, str) or not command or not isinstance(args, list):
            continue
        if not all(isinstance(item, str) for item in args) or not isinstance(env, dict):
            continue
        if not isinstance(command_allowlist, list) or not all(
            isinstance(item, str) for item in command_allowlist
        ):
            continue
        if not isinstance(env_allowlist, list) or not all(
            isinstance(item, str) for item in env_allowlist
        ):
            continue
        if not isinstance(resources, dict):
            resources = {}
        safe_env = {
            str(key): str(value)
            for key, value in env.items()
            if str(key) in {str(item) for item in env_allowlist}
        }
        timeout_seconds = resources.get("timeout_seconds", raw.get("timeout_seconds", 20))
        cpu_seconds = resources.get("cpu_seconds", raw.get("cpu_seconds"))
        memory_mb = resources.get("memory_mb", raw.get("memory_mb"))
        configs.append(
            MCPServerConfig(
                name=str(name),
                command=command,
                args=tuple(args),
                env=safe_env,
                cwd=config_path.parent,
                command_allowlist=tuple(command_allowlist),
                env_allowlist=tuple(env_allowlist),
                timeout_seconds=timeout_seconds if isinstance(timeout_seconds, int) else 20,
                cpu_seconds=cpu_seconds if isinstance(cpu_seconds, int) else None,
                memory_mb=memory_mb if isinstance(memory_mb, int) else None,
                trusted=str(name) in trusted_servers,
            )
        )
    return configs


def _preexec_limits(config: MCPServerConfig):
    if platform.system() == "Windows" or resource is None:
        return None

    def apply_limits() -> None:
        if config.cpu_seconds is not None and config.cpu_seconds > 0:
            resource.setrlimit(resource.RLIMIT_CPU, (config.cpu_seconds, config.cpu_seconds))
        if config.memory_mb is not None and config.memory_mb > 0:
            limit = config.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return apply_limits


def _command_allowed(config: MCPServerConfig) -> bool:
    return bool(config.command_allowlist) and config.command in config.command_allowlist


async def _send(proc: asyncio.subprocess.Process, message: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise MCPProtocolError("MCP server stdin is unavailable")
    proc.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    await proc.stdin.drain()


async def _receive(
    proc: asyncio.subprocess.Process, request_id: int, timeout_seconds: int
) -> dict[str, Any]:
    if proc.stdout is None:
        raise MCPProtocolError("MCP server stdout is unavailable")
    while True:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout_seconds)
        if not line:
            raise MCPProtocolError("MCP server exited before responding")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != request_id:
            continue
        if "error" in message:
            raise MCPProtocolError(str(message["error"]))
        result = message.get("result", {})
        return result if isinstance(result, dict) else {"value": result}


async def _request(
    proc: asyncio.subprocess.Process,
    request_id: int,
    method: str,
    params: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    await _send(
        proc,
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
    )
    return await _receive(proc, request_id, timeout_seconds)


async def _with_server(
    config: MCPServerConfig,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    process_kwargs: dict[str, Any] = {
        "cwd": str(config.cwd),
        "env": config.env,
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.DEVNULL,
    }
    if platform.system() != "Windows":
        process_kwargs["start_new_session"] = True
        process_kwargs["preexec_fn"] = _preexec_limits(config)
    proc = await asyncio.create_subprocess_exec(config.command, *config.args, **process_kwargs)
    try:
        await _request(
            proc,
            1,
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": client_info(),
            },
            config.timeout_seconds,
        )
        await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        return await _request(proc, 2, method, params, config.timeout_seconds)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except TimeoutError:
                proc.kill()
                await proc.wait()


def _schema_model(server_name: str, tool: dict[str, Any]) -> type:
    schema = tool.get("inputSchema", {})
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    fields: dict[str, tuple[Any, Any]] = {}
    if isinstance(properties, dict):
        for name, details in properties.items():
            description = details.get("description", "") if isinstance(details, dict) else ""
            default = ... if name in required else None
            fields[str(name)] = (Any, Field(default=default, description=str(description)))
    model_name = f"MCP_{_NAME_RE.sub('_', server_name)}_{_NAME_RE.sub('_', str(tool['name']))}"
    return create_model(model_name, __config__=ConfigDict(extra="allow"), **fields)


def _render_result(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        texts = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
        if texts:
            return "\n".join(item for item in texts if item)
    return json.dumps(result, ensure_ascii=False, default=str)


async def load_mcp_tools(workspace: str | Path) -> list[BaseTool]:
    if os.getenv("REASONING_ENABLE_MCP") != "1":
        return []
    tools: list[BaseTool] = []
    for config in _load_configs(Path(workspace).resolve()):
        if not config.trusted:
            record_audit_event(
                event_type="mcp.first_connection",
                actor="agent",
                resource_id=f"mcp:{config.name}",
                outcome="denied",
                rule="mcp_first_trust_required",
                approval_result="deny",
            )
            continue
        if not _command_allowed(config):
            record_audit_event(
                event_type="mcp.first_connection",
                actor="agent",
                resource_id=f"mcp:{config.name}",
                outcome="denied",
                rule="mcp_command_allowlist",
                approval_result="deny",
            )
            continue
        record_audit_event(
            event_type="mcp.first_connection",
            actor="agent",
            resource_id=f"mcp:{config.name}",
            outcome="allowed",
            rule="mcp_first_trust_required",
            approval_result="allow",
        )
        try:
            result = await _with_server(config, "tools/list", {})
        except (OSError, TimeoutError, MCPProtocolError):
            logger.error("Unable to discover MCP server %s", config.name)
            continue
        raw_tools = result.get("tools", [])
        if not isinstance(raw_tools, list):
            continue
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, dict) or not raw_tool.get("name"):
                continue
            remote_name = str(raw_tool["name"])
            public_name = _NAME_RE.sub("_", f"mcp__{config.name}__{remote_name}")[:64]

            async def call_remote(
                _config: MCPServerConfig = config,
                _remote_name: str = remote_name,
                **kwargs: Any,
            ) -> str:
                response = await _with_server(
                    _config,
                    "tools/call",
                    {"name": _remote_name, "arguments": kwargs},
                )
                return _render_result(response)

            tools.append(
                StructuredTool(
                    name=public_name,
                    description=str(raw_tool.get("description", "MCP tool")),
                    args_schema=_schema_model(config.name, raw_tool),
                    coroutine=call_remote,
                )
            )
    return tools
