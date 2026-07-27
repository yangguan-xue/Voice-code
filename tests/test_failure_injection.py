from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_isolated(code: str, tmp_path: Path) -> dict:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(sys.path),
        "REASONING_HOME": str(tmp_path / "reasoning-home"),
        "REASONING_MEMORY_DB": str(tmp_path / "memory.db"),
        "REASONING_TRANSCRIPT_DIR": str(tmp_path / "transcripts"),
    }
    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_provider_4xx_5xx_exercise_fallback_and_terminal_error(tmp_path: Path) -> None:
    code = r'''
import asyncio
import json
from langchain_core.messages import AIMessageChunk
from voice_code.agent.loop import agent_loop

class ProviderError(Exception):
    def __init__(self, status_code):
        super().__init__(f"provider status {status_code} secret-token user prompt")
        self.status_code = status_code

class FailingModel:
    model_name = "primary"
    def __init__(self, status_code):
        self.status_code = status_code
    async def astream(self, _messages, **_kwargs):
        raise ProviderError(self.status_code)
        yield

class FallbackModel:
    model_name = "fallback"
    async def astream(self, _messages, **_kwargs):
        yield AIMessageChunk(content="fallback ok")

async def collect(status_code, fallback):
    events = []
    async for event in agent_loop(
        "hello",
        [],
        "system",
        FailingModel(status_code),
        fallback_model=fallback,
        llm_timeout_seconds=1.0,
    ):
        events.append(
            {
                "type": event.type.value,
                "content": event.content,
                "status": event.status,
                "error_code": str(event.error_code or ""),
                "finish_reason": event.finish_reason,
            }
        )
    return events

async def main():
    rate_limited = await collect(429, FallbackModel())
    server_error = await collect(500, None)
    print(json.dumps({"rate_limited": rate_limited, "server_error": server_error}))

asyncio.run(main())
'''

    result = _run_isolated(code, tmp_path)

    assert any(event["status"] == "fallback" for event in result["rate_limited"])
    assert any(event["content"] == "fallback ok" for event in result["rate_limited"])
    assert any(event["finish_reason"] == "completed" for event in result["rate_limited"])
    server_errors = [
        event for event in result["server_error"] if event["status"] == "generic_error"
    ]
    assert server_errors[0]["error_code"] == "PROVIDER_UNAVAILABLE"
    assert "secret-token" not in json.dumps(result)
    assert "user prompt" not in json.dumps(result)


def test_sqlite_lock_failure_dead_letters_and_health_are_traceable(tmp_path: Path) -> None:
    code = r'''
import asyncio
import json
import sqlite3
from pathlib import Path
from voice_code.memory.candidates import ExtractionMode
from voice_code.memory.extraction import ExtractionUnavailableError
from voice_code.memory.repository import MemoryRepository
from voice_code.memory.worker import MemoryExtractionWorker
from voice_code.status import build_health_status

class Provider:
    async def extract(self, _turn):
        raise ExtractionUnavailableError("provider 5xx secret user text")

async def main():
    database_path = Path(__import__("os").environ["REASONING_MEMORY_DB"])
    repository = MemoryRepository(database_path)
    repository.enqueue_extraction_job(
        user_id="local",
        project_key=None,
        source_session_id="session-1",
        source_turn_id="turn-1",
        user_input="do not leak me",
        assistant_response="ok",
        mode=ExtractionMode.AUTOMATIC,
    )
    holder = sqlite3.connect(database_path, timeout=0.1)
    holder.execute("BEGIN EXCLUSIVE")
    holder.execute("CREATE TABLE IF NOT EXISTS lock_probe(value INTEGER)")
    try:
        try:
            repository.claim_extraction_jobs(limit=1, lease_seconds=30)
            locked_error = ""
        except sqlite3.OperationalError as exc:
            locked_error = str(exc)
    finally:
        holder.rollback()
        holder.close()
    await MemoryExtractionWorker(repository, Provider(), max_attempts=1).run_once(limit=1)
    dead_letters = repository.list_dead_letters()
    health = (
        await build_health_status(
            repository=repository,
            transcript_dir=database_path.parent / "transcripts",
        )
    ).to_dict()
    print(
        json.dumps(
            {
                "locked_error": locked_error,
                "dead_letters": dead_letters,
                "rag_backlog": health["components"]["rag_backlog"],
            }
        )
    )

asyncio.run(main())
'''

    result = _run_isolated(code, tmp_path)

    assert "locked" in result["locked_error"].lower()
    assert result["dead_letters"][0]["source_type"] == "extraction"
    assert result["dead_letters"][0]["attempts"] == 1
    assert result["dead_letters"][0]["error_code"] == "ExtractionUnavailableError"
    assert result["rag_backlog"]["dead_letter_count"] == 1
    assert "do not leak me" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_mcp_config_tampering_is_denied_and_audited_in_real_discovery(
    tmp_path: Path,
) -> None:
    code = r'''
import asyncio
import json
import os
import sys
from pathlib import Path
from voice_code.audit import capture_audit_events
from voice_code.integrations.mcp import load_mcp_tools, trust_mcp_server

async def main():
    workspace = Path(os.environ["REASONING_HOME"])
    workspace.mkdir(parents=True, exist_ok=True)
    server = workspace / "server.py"
    server.write_text("import sys\nfor _ in sys.stdin: pass\n", encoding="utf-8")
    config = {
        "mcpServers": {
            "fake": {
                "command": sys.executable,
                "args": [str(server)],
                "command_allowlist": ["/bin/false"],
            }
        }
    }
    (workspace / ".mcp.json").write_text(json.dumps(config), encoding="utf-8")
    trust_mcp_server(workspace, "fake")
    os.environ["REASONING_ENABLE_MCP"] = "1"
    with capture_audit_events() as events:
        tools = await load_mcp_tools(workspace)
    print(json.dumps({"tools": [tool.name for tool in tools], "events": events}))

asyncio.run(main())
'''

    result = _run_isolated(code, tmp_path)

    assert result["tools"] == []
    assert result["events"][0]["event_type"] == "mcp.first_connection"
    assert result["events"][0]["outcome"] == "denied"
    assert result["events"][0]["rule"] == "mcp_command_allowlist"


def test_bash_sandbox_escape_is_rejected_and_write_audit_is_traceable(
    tmp_path: Path,
) -> None:
    code = r'''
import json
from pathlib import Path
from voice_code.audit import capture_audit_events
from voice_code.security import workspace_boundary
from voice_code.tools.bash import BashSandboxPolicy, bash

workspace = Path(__import__("os").environ["REASONING_HOME"]) / "workspace"
allowed = workspace / "allowed"
outside = workspace / "outside"
allowed.mkdir(parents=True, exist_ok=True)
outside.mkdir(parents=True, exist_ok=True)
with capture_audit_events() as events:
    with workspace_boundary(workspace):
        rejected = bash.invoke(
            {
                "command": "pwd",
                "workdir": str(outside),
                "policy": BashSandboxPolicy(allowlist=("allowed",)),
            }
        )
        written = bash.invoke({"command": "printf ok > marker", "workdir": "allowed"})
print(
    json.dumps(
        {
            "rejected": rejected,
            "written": written,
            "events": events,
            "marker_exists": (allowed / "marker").exists(),
        }
    )
)
'''

    result = _run_isolated(code, tmp_path)

    assert "outside the Bash allowlist" in result["rejected"]
    assert result["marker_exists"] is True
    assert any(event["event_type"] == "bash.write" for event in result["events"])
    assert all("outside" not in event.get("resource_id", "") for event in result["events"])
