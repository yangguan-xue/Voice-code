from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from langchain_core.tools import tool

from voice_code.commands import parse_command
from voice_code.delegation.worktree import WorktreeManager
from voice_code.goals import GoalLoop, GoalSpec, GoalStore
from voice_code.goals.controller import decide_next_action
from voice_code.goals.types import (
    GoalAction,
    GoalState,
    VerificationEvidence,
)
from voice_code.integrations.mcp import load_mcp_tools, trust_mcp_server
from voice_code.memory.select import rerank_candidates
from voice_code.permissions import PermissionBehavior, PermissionRequest
from voice_code.prompt_cache import cached_prompt
from voice_code.prompts import get_system_prompt
from voice_code.security import (
    WorkspaceBoundaryError,
    redact_secrets,
    resolve_workspace_path,
    workspace_boundary,
)
from voice_code.skills import discover_skills, render_skills_prompt
from voice_code.subagents.definitions import (
    filter_tools_for_definition,
    get_agent_definition,
)
from voice_code.voice.delegation import (
    DelegationPermissionApprover,
    create_voice_delegation_brief,
)


def test_workspace_boundary_resolves_relative_paths_and_rejects_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        with workspace_boundary(tmp_path):
            assert resolve_workspace_path("notes.txt") == tmp_path / "notes.txt"
            with pytest.raises(WorkspaceBoundaryError):
                resolve_workspace_path(outside)
    finally:
        outside.unlink(missing_ok=True)


def test_secret_redaction_preserves_shape() -> None:
    payload = {
        "header": "Authorization: Bearer live-token",
        "nested": ["api_key=top-secret", "safe"],
    }

    redacted = redact_secrets(payload)

    assert redacted == {
        "header": "Authorization: Bearer [REDACTED]",
        "nested": ["api_key=[REDACTED]", "safe"],
    }


def test_goal_and_memory_commands_have_structured_arguments() -> None:
    goal = parse_command(
        '/goal --allow src --allow tests --verify "uv run ruff check src/" '
        "--max-iterations 7 fix lint"
    )
    remember = parse_command("/remember user prefer concise replies")

    assert goal.name == "goal"
    assert goal.args == {
        "objective": "fix lint",
        "verification_commands": ["uv run ruff check src/"],
        "allowed_paths": ["src", "tests"],
        "max_iterations": 7,
    }
    assert remember.name == "remember"
    assert remember.args == {"scope": "user", "text": "prefer concise replies"}


@pytest.mark.asyncio
async def test_goal_loop_completes_and_persists_evidence(tmp_path: Path) -> None:
    spec = GoalSpec(
        goal_id="goal-pass",
        objective="produce an acceptable result",
        workspace=str(tmp_path),
        verification_commands=[f'"{sys.executable}" -c "raise SystemExit(0)"'],
        max_iterations=2,
    )

    async def builder(prompt: str) -> str:
        return f"done: {prompt}"

    async def reviewer(_spec: GoalSpec, _result: str) -> list[str]:
        return []

    result = await GoalLoop(spec).run(builder, reviewer)
    store = GoalStore(tmp_path)

    assert str(result.state.status) == "completed"
    assert store.load_state(spec.goal_id).iteration == 1
    assert (store.goal_dir(spec.goal_id) / "evidence" / "iteration-001.json").is_file()


def test_goal_controller_escalates_repeated_identical_failure() -> None:
    spec = GoalSpec(
        goal_id="goal-repeat",
        objective="pass",
        workspace=".",
        verification_commands=["false"],
        max_consecutive_same_failure=2,
    )
    evidence = [VerificationEvidence("false", 1, "", "failed", 1)]
    initial = decide_next_action(spec, GoalState(goal_id=spec.goal_id), evidence)
    state = GoalState(
        goal_id=spec.goal_id,
        last_failure_signature=initial.failure_signature,
        repeated_failure_count=1,
    )

    repeated = decide_next_action(spec, state, evidence)

    assert repeated.action == GoalAction.ESCALATE


def test_prompt_cache_builds_once_for_identical_inputs(tmp_path: Path) -> None:
    calls = 0

    def builder() -> str:
        nonlocal calls
        calls += 1
        return "assembled prompt"

    first = cached_prompt(workspace=tmp_path, inputs={"model": "test"}, builder=builder)
    second = cached_prompt(workspace=tmp_path, inputs={"model": "test"}, builder=builder)

    assert first == second == "assembled prompt"
    assert calls == 1
    assert next((tmp_path / ".reasoning" / "cache" / "prompts").glob("*.txt")).is_file()


def test_skill_discovery_ignores_symlink_escape_and_renders_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REASONING_HOME", str(tmp_path / "empty-home"))
    skills_root = tmp_path / ".reasoning" / "skills"
    safe = skills_root / "safe"
    safe.mkdir(parents=True)
    (safe / "SKILL.md").write_text("Follow the safe workflow.", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-skill"
    outside.mkdir()
    (outside / "SKILL.md").write_text("escaped", encoding="utf-8")
    (skills_root / "escaped").symlink_to(outside, target_is_directory=True)
    try:
        skills = discover_skills(tmp_path)
    finally:
        (skills_root / "escaped").unlink(missing_ok=True)
        (outside / "SKILL.md").unlink(missing_ok=True)
        outside.rmdir()

    assert [item.name for item in skills] == ["safe"]
    prompt = render_skills_prompt(skills)
    assert "## Skill: safe" in prompt
    assert "Follow the safe workflow." in prompt
    assert prompt in get_system_prompt(skills_prompt=prompt)


def test_subagent_background_tool_filter_removes_nested_agent_and_questions() -> None:
    @tool
    def agent() -> str:
        """Spawn another agent."""
        return ""

    @tool
    def ask_user() -> str:
        """Ask the user."""
        return ""

    @tool
    def read() -> str:
        """Read data."""
        return ""

    filtered = filter_tools_for_definition(
        [agent, ask_user, read],
        get_agent_definition("researcher"),
        background=True,
    )

    assert [item.name for item in filtered] == ["read"]


def test_memory_reranking_exposes_score_and_orders_relevance() -> None:
    candidates = [
        {"id": "low", "name": "general notes", "description": "misc"},
        {"id": "high", "name": "cache tests", "description": "cache boundary tests"},
    ]

    selected = rerank_candidates("cache tests", candidates, top_k=2)

    assert selected[0]["id"] == "high"
    assert isinstance(selected[0]["_memory_score"], float)


def test_voice_delegation_requires_confirmation_and_blocks_high_risk() -> None:
    brief = create_voice_delegation_brief("后台帮我 git push", workspace="/tmp/project")
    decision = DelegationPermissionApprover().approve(
        PermissionRequest(
            tool_name="bash",
            tool_input={"command": "git push origin main"},
            risk_category="medium",
        )
    )

    assert brief.confirmation_required is True
    assert decision.behavior == PermissionBehavior.DENY
    with pytest.raises(ValueError):
        WorktreeManager._validate_task_id("../escape")


@pytest.mark.asyncio
async def test_worktree_delegation_patch_includes_new_files_and_applies(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repository,
        check=True,
    )
    (repository / ".gitignore").write_text(".reasoning/\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)
    manager = WorktreeManager(repository)

    worktree = await manager.create("task-1")
    (worktree / "tracked.txt").write_text("after\n", encoding="utf-8")
    (worktree / "new.txt").write_text("new\n", encoding="utf-8")
    patch = await manager.create_patch("task-1")
    await manager.apply_patch(patch)

    assert (repository / "tracked.txt").read_text(encoding="utf-8") == "after\n"
    assert (repository / "new.txt").read_text(encoding="utf-8") == "new\n"
    await manager.discard("task-1")
    assert not worktree.exists()


@pytest.mark.asyncio
async def test_stdio_mcp_tool_discovery_and_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = tmp_path / "fake_mcp.py"
    server.write_text(
        """\
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2025-03-26", "capabilities": {}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo input", "inputSchema": {
            "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]
        }}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": message["params"]["arguments"]["value"]}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    server.chmod(server.stat().st_mode | stat.S_IXUSR)
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fake": {
                        "command": sys.executable,
                        "args": [str(server)],
                        "command_allowlist": [sys.executable],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    trust_mcp_server(tmp_path, "fake")
    monkeypatch.setenv("REASONING_ENABLE_MCP", "1")

    tools = await load_mcp_tools(tmp_path)

    assert [item.name for item in tools] == ["mcp__fake__echo"]
    assert await tools[0].ainvoke({"value": "hello"}) == "hello"
