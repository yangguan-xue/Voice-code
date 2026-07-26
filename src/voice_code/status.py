"""Engineering progress dashboard data and formatters."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from voice_code import __version__
from voice_code.memory.config import MemoryRagConfig
from voice_code.memory.repository import MemoryRepository
from voice_code.session import get_transcript_dir


@dataclass(frozen=True)
class StatusTrack:
    track: str
    status: str
    specs: tuple[str, ...]
    code_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    biggest_gap: str
    next_step: str


@dataclass(frozen=True)
class StatusArea:
    label: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class HealthStatus:
    schema_version: str
    status: str
    generated_at: str
    version: dict[str, str]
    runtime: dict[str, str]
    components: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "generated_at": self.generated_at,
            "version": dict(self.version),
            "runtime": dict(self.runtime),
            "components": {name: dict(value) for name, value in self.components.items()},
        }


STATUS_SUMMARY = (
    "All 10 rounds of production engineering delivered with the local test gate passing. "
    "CLI / TUI / Voice three-entry stable; telemetry / supervisor / DLQ / security / "
    "data governance / CI / runbook closed. Real SLO approval and RAG `automatic` "
    "shadow require 7 days of real internal traffic."
)


STATUS_TRACKS: tuple[StatusTrack, ...] = (
    StatusTrack(
        track="tui-premium-ui",
        status="complete",
        specs=("32", "33", "34", "35", "36"),
        code_paths=(
            "new/src/voice_code/tui.py",
            "new/src/voice_code/theme.py",
            "new/src/voice_code/tui_sessions.py",
            "new/src/voice_code/tui_message_components.py",
        ),
        test_paths=(
            "new/tests/test_tui_sessions.py",
            "new/tests/test_tui_transcript_behavior.py",
            "new/tests/test_tui_event_metrics.py",
            "new/tests/test_tui_message_components.py",
            "new/tests/test_tui_message_widgets.py",
        ),
        biggest_gap="task, permission, and history surfaces still need some UX polish",
        next_step="keep future work to local UX polish instead of reworking the layout",
    ),
    StatusTrack(
        track="session-runtime-resume-v2",
        status="complete",
        specs=("37", "41"),
        code_paths=(
            "new/src/voice_code/session/state.py",
            "new/src/voice_code/session/resume.py",
            "new/src/voice_code/commands.py",
            "new/src/voice_code/cli.py",
            "new/src/voice_code/tui.py",
        ),
        test_paths=(
            "new/tests/test_session_resume.py",
            "new/tests/test_tui_event_metrics.py",
            "new/tests/test_tui_transcript_behavior.py",
            "new/tests/test_loop.py",
        ),
        biggest_gap=(
            "resume restores a working session view, but it does not resume background"
            " execution across interruption"
        ),
        next_step="keep cross-interruption continuation on the subagent platform roadmap",
    ),
    StatusTrack(
        track="permission-engine-v2",
        status="complete",
        specs=("38", "42"),
        code_paths=(
            "new/src/voice_code/permissions.py",
            "new/src/voice_code/tui_permission_dialog.py",
            "new/src/voice_code/commands.py",
            "new/src/voice_code/cli.py",
            "new/src/voice_code/tui.py",
        ),
        test_paths=(
            "new/tests/test_permissions.py",
            "new/tests/test_tui_permissions.py",
            "new/tests/test_commands.py",
            "new/tests/test_mcp_security.py",
            "new/tests/test_audit_log.py",
        ),
        biggest_gap=(
            "round 7 added Bash sandbox (allowlist + resource limits + network policy),"
            " MCP first-trust + audit chain. Rule-creation UX still has room for"
            " higher-level presets."
        ),
        next_step="add rule-create presets and richer bash risk explanations",
    ),
    StatusTrack(
        track="subagent-platform",
        status="complete",
        specs=("40", "44"),
        code_paths=(
            "new/src/voice_code/subagents/",
            "new/src/voice_code/tools/agent.py",
            "new/src/voice_code/tools/task_list.py",
            "new/src/voice_code/tools/task_get.py",
            "new/src/voice_code/tools/task_stop.py",
            "new/src/voice_code/tui_runtime.py",
            "new/src/voice_code/tui.py",
        ),
        test_paths=(
            "new/tests/test_subagent_tools.py",
            "new/tests/test_subagent_runtime.py",
            "new/tests/test_subagent_registry.py",
            "new/tests/test_subagent_planner.py",
            "new/tests/test_tui_event_metrics.py",
        ),
        biggest_gap=(
            "subagent tasks are explicitly temporary: status --json exposes"
            " subagent_recovery=unrecoverable. Cross-process continuation is"
            " deliberately out of scope for current single-user architecture."
        ),
        next_step="defer cross-process continuation to multi-tenant rounds (4-6)",
    ),
    StatusTrack(
        track="agent-loop-runtime",
        status="complete",
        specs=("39", "43"),
        code_paths=(
            "new/src/voice_code/agent/loop.py",
            "new/src/voice_code/subagents/service.py",
        ),
        test_paths=("new/tests/test_loop.py",),
        biggest_gap=(
            "agent loop now passes permission_context / fallback_model / "
            "telemetry context explicitly. Goal builder addendum requires"
            " re-reading implementation to catch test/impl mismatches."
        ),
        next_step="no structural change; rely on goal_prompt + reviewer gate",
    ),
    StatusTrack(
        track="telemetry-health",
        status="complete",
        specs=("4-round delivery",),
        code_paths=(
            "new/src/voice_code/telemetry/",
            "new/src/voice_code/status.py",
        ),
        test_paths=(
            "new/tests/test_telemetry_instrumentation.py",
            "new/tests/test_telemetry_privacy.py",
            "new/tests/test_telemetry_runtime.py",
            "new/tests/test_round4_telemetry_health.py",
        ),
        biggest_gap=(
            "round 4 added 9 panel metrics (agent/llm/tool/permission/session/voice/rag)"
            " plus unified --json health exposing 9 components. Privacy gate:"
            " telemetry / health never contain user content, secrets, voice text, or"
            " full tool output."
        ),
        next_step="dashboard and alert wiring under round 10 ops documentation",
    ),
    StatusTrack(
        track="task-supervisor-and-dlq",
        status="complete",
        specs=("5-round + 6-round delivery",),
        code_paths=(
            "new/src/voice_code/task_supervisor.py",
            "new/src/voice_code/memory/worker.py",
            "new/src/voice_code/memory/rag_service.py",
            "new/src/voice_code/memory/cli.py",
        ),
        test_paths=(
            "new/tests/test_task_supervisor.py",
            "new/tests/test_round6_worker_recovery.py",
            "new/tests/test_memory_commands_v3.py",
        ),
        biggest_gap=(
            "round 5 unified task lifecycle (start_soon strict + create_task adapter)."
            " Round 6 added extraction worker DLQ + outbox terminal DLQ via real"
            " process exit + restart test (os._exit(23)). DLQ CLI: list, explain,"
            " replay. status --json exposes dead_letter_count and"
            " subagent_recovery semantics."
        ),
        next_step="long-running observation; tune retry/backoff thresholds with real traffic",
    ),
    StatusTrack(
        track="security-and-data-governance",
        status="complete",
        specs=("7-round + 8-round delivery",),
        code_paths=(
            "new/src/voice_code/audit.py",
            "new/src/voice_code/tools/bash.py",
            "new/src/voice_code/integrations/mcp.py",
            "new/src/voice_code/llm/models.py",
            "new/src/voice_code/data_retention.py",
            "new/src/voice_code/session/lifecycle.py",
        ),
        test_paths=(
            "new/tests/test_tools_bash.py",
            "new/tests/test_mcp_security.py",
            "new/tests/test_audit_log.py",
            "new/tests/test_models.py",
            "new/tests/test_data_retention.py",
            "new/tests/test_session_lifecycle.py",
            "new/tests/test_round8_data_governance.py",
        ),
        biggest_gap=(
            "round 7: bash sandbox + MCP trust + secrets detection + audit chain."
            " Round 8: data retention + transcript recovery + session export/delete +"
            " SQLite backup + Milvus rebuild. models.toml rejects plaintext api_key;"
            " all secrets must use api_key_env reference."
        ),
        next_step="deploy dashboards; run 7-day baseline collection for SLO approval",
    ),
    StatusTrack(
        track="ci-cd-and-runbook",
        status="complete",
        specs=("9-round + 10-round delivery",),
        code_paths=(
            "new/.github/workflows/ci.yml",
            "new/.github/scripts/ci_smoke.py",
            "new/scripts/backup-and-restore.sh",
            "new/scripts/rollback-previous-stable.sh",
            "new/docs/dashboard-spec.md",
            "new/docs/slo-baseline.md",
            "new/docs/inspection-checkpoint-c.md",
            "new/docs/runbooks/",
        ),
        test_paths=(
            "new/tests/test_release_readiness.py",
            "new/tests/test_failure_injection.py",
        ),
        biggest_gap=(
            "round 9: 8-job CI (lint/types/tests/coverage/secret/license/build/smoke),"
            " single version source, rollback script. Round 10: 9-panel dashboard"
            " spec, SLO baseline, 11+ symptom-driven runbooks, 253-line real"
            " subprocess failure injection tests. Checkpoint C all 6 subitems"
            " checked. RAG `automatic` and formal SLO still require 7-day observation."
        ),
        next_step="run dashboard; collect 7 days of real internal traffic for SLO approval",
    ),
)


STATUS_AREAS: tuple[StatusArea, ...] = (
    StatusArea(
        label="Mature Areas",
        items=(
            "tui-premium-ui: unified visual language, session sidebar, and stable transcript"
            " hierarchy",
            "session-runtime-resume-v2: state sidecar plus shared resume service across"
            " entry points",
            "permission-engine-v2: structured evaluation, persistence, TUI approval,"
            " bash sandbox, MCP first-trust, audit chain",
            "telemetry-health: 9 panel metrics, unified --json health, privacy gate",
            "task-supervisor-and-dlq: unified task lifecycle, extraction worker DLQ,"
            " outbox terminal DLQ, DLQ CLI list/explain/replay",
            "security-and-data-governance: bash sandbox, MCP trust, secrets detection,"
            " data retention, session export/delete, backup + Milvus rebuild",
            "ci-cd-and-runbook: 8-job CI, single version source, rollback script,"
            " dashboard spec, SLO baseline, 11+ runbooks, failure injection tests",
        ),
    ),
    StatusArea(
        label="Partial Areas",
        items=(
            "rule authoring UX: add flow exists, but higher-level presets and templates"
            " are still missing",
            "subagent continuation across interruption: deliberately out of scope for"
            " current single-user architecture; deferred to multi-tenant rounds",
        ),
    ),
    StatusArea(
        label="Fragile Areas",
        items=(
            "external gateway stability: deepseek official / yangguanxue.top / aijws /"
            " sudocode have all dropped during 10-round delivery. Production deploy"
            " needs a stable internal LLM gateway.",
            "RAG `automatic` mode: only `shadow` mode is safe today; needs 7-day"
            " observation + 500 review turns before promotion.",
        ),
    ),
)


RECOMMENDED_PRIORITIES: tuple[str, ...] = (
    "external gateway stability: deploy a stable internal LLM gateway before any real"
    " internal traffic ramp",
    "rule authoring UX: add rule-create presets and templates for common bash"
    " patterns",
    "7-day observation: collect real SLO baseline and run RAG `automatic` in shadow",
    "multi-tenant rounds: identity, organization policy, centralized control plane"
    " (4-6 rounds, not yet started)",
)


STATUS_ENUMS: tuple[tuple[str, str], ...] = (
    ("complete", "Main capability is stable for day-to-day use."),
    ("mostly_complete", "Core paths are solid, but an obvious gap remains."),
    ("partial", "Usable, but not yet a stable platform surface."),
    ("toy_or_fragile", "Direction exists, but the overall capability is still fragile."),
)


def _short_paths(paths: tuple[str, ...]) -> str:
    return ", ".join(path.rsplit("/", 1)[-1] if "/" in path else path for path in paths)


def _join_paths(paths: tuple[str, ...]) -> str:
    return ", ".join(f"`{path}`" for path in paths)


def format_status_overview_lines() -> list[str]:
    lines = [
        "Engineering Progress Dashboard",
        STATUS_SUMMARY,
        "",
    ]
    for track in STATUS_TRACKS:
        lines.extend(
            [
                f"{track.track} [{track.status}]",
                f"  specs: {', '.join(track.specs)}",
                f"  code: {_short_paths(track.code_paths)}",
                f"  tests: {_short_paths(track.test_paths)}",
                f"  gap: {track.biggest_gap}",
                f"  next: {track.next_step}",
                "",
            ]
        )
    return lines[:-1]


def format_status_area_lines() -> list[str]:
    lines: list[str] = []
    for area in STATUS_AREAS:
        if lines:
            lines.append("")
        lines.append(area.label)
        for item in area.items:
            lines.append(f"- {item}")
    return lines


def format_status_gap_lines() -> list[str]:
    lines = ["Biggest Gaps"]
    for track in STATUS_TRACKS:
        lines.append(f"- {track.track}: {track.biggest_gap}")
    lines.extend(["", "Recommended Priorities"])
    for index, item in enumerate(RECOMMENDED_PRIORITIES, start=1):
        lines.append(f"{index}. {item}")
    return lines


def render_status_markdown(updated_at: str | None = None) -> str:
    updated = updated_at or date.today().isoformat()

    lines = [
        "# Current Progress Status",
        "",
        f"更新时间：{updated}",
        "",
        "<!-- Generated from new/src/voice_code/status.py -->",
        "",
        "## Summary",
        "",
        STATUS_SUMMARY,
        "",
        "Status enums:",
        "",
    ]

    for status, description in STATUS_ENUMS:
        lines.append(f"- `{status}`: {description}")

    lines.extend(
        [
            "",
            "## Tracks",
            "",
            "| Track | Status | Evidence | Biggest Gap | Next Step |",
            "| --- | --- | --- | --- | --- |",
        ]
    )

    for track in STATUS_TRACKS:
        evidence = (
            f"Specs: `{', '.join(track.specs)}`. "
            f"Code: {_join_paths(track.code_paths)}. "
            f"Tests: {_join_paths(track.test_paths)}."
        )
        lines.append(
            f"| `{track.track}` | `{track.status}` | {evidence} | "
            f"{track.biggest_gap} | {track.next_step} |"
        )

    for area in STATUS_AREAS:
        lines.extend(["", f"## {area.label}", ""])
        for item in area.items:
            lines.append(f"- {item}")

    lines.extend(["", "## Recommended Priorities", ""])
    for index, item in enumerate(RECOMMENDED_PRIORITIES, start=1):
        lines.append(f"{index}. {item}")

    lines.extend(
        [
            "",
            "## Notes For Agents",
            "",
            "- Prefer repo evidence over chat memory when judging maturity.",
            "- For every new track, add spec, code, tests, biggest gap, and next step.",
            "- If test evidence is missing, lower certainty instead of calling it `complete`.",
            "",
        ]
    )

    return "\n".join(lines)


def write_status_markdown(doc_path: Path, updated_at: str | None = None) -> None:
    doc_path.write_text(render_status_markdown(updated_at=updated_at), encoding="utf-8")


def _component(status: str, **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status}
    payload.update(fields)
    return payload


def _overall_status(components: dict[str, dict[str, Any]]) -> str:
    statuses = {str(component.get("status", "unknown")) for component in components.values()}
    if "unhealthy" in statuses:
        return "unhealthy"
    if "degraded" in statuses or "unknown" in statuses:
        return "degraded"
    return "healthy"


def _dependency_status(healthy: bool | None) -> str:
    if healthy is True:
        return "healthy"
    if healthy is None:
        return "degraded"
    return "unhealthy"


def _sqlite_health(repository: Any) -> dict[str, Any]:
    try:
        database_path = Path(repository.database_path)
        with sqlite3.connect(database_path) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            connection.execute("CREATE TEMP TABLE IF NOT EXISTS health_probe(value INTEGER)")
            connection.execute("INSERT INTO health_probe(value) VALUES (1)")
            connection.execute("DELETE FROM health_probe")
        return _component("healthy", schema_version=version, writable=True)
    except Exception:
        return _component("unhealthy", schema_version="unknown", writable=False)


def _backlog_health(repository: Any) -> dict[str, Any]:
    try:
        with sqlite3.connect(Path(repository.database_path)) as connection:
            outbox = connection.execute(
                "SELECT COUNT(*), MIN(created_at) FROM memory_index_outbox "
                "WHERE delivered_at IS NULL"
            ).fetchone()
            extraction = connection.execute(
                "SELECT COUNT(*), MIN(created_at) FROM memory_extraction_jobs "
                "WHERE status != 'completed'"
            ).fetchone()
        return _component(
            "healthy",
            outbox_backlog=int(outbox[0]),
            outbox_oldest_created_at=outbox[1],
            extraction_backlog=int(extraction[0]),
            extraction_oldest_created_at=extraction[1],
            dead_letter_count=int(repository.dead_letter_count()),
        )
    except Exception:
        return _component("unknown", outbox_backlog=0, extraction_backlog=0, dead_letter_count=0)


def _transcript_health(transcript_dir: Path) -> dict[str, Any]:
    try:
        transcript_dir.mkdir(parents=True, exist_ok=True)
        probe = transcript_dir / ".health_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        usage = shutil.disk_usage(transcript_dir)
        free_ratio = usage.free / usage.total if usage.total else 0.0
        status = "healthy" if free_ratio >= 0.05 else "degraded"
        return _component(status, writable=True, free_bytes=usage.free)
    except Exception:
        return _component("unhealthy", writable=False, free_bytes=0)


async def _milvus_health(vector_index: Any) -> dict[str, Any]:
    if vector_index is None:
        return _component("healthy", enabled=False)
    try:
        healthy = await vector_index.health()
        return _component("healthy" if healthy else "degraded", enabled=True)
    except Exception:
        return _component("degraded", enabled=True)


async def build_health_status(
    *,
    repository: Any,
    vector_index: Any | None = None,
    transcript_dir: Path | str,
    llm_provider: str = "unknown",
    llm_healthy: bool | None = None,
    voice_provider: str = "disabled",
    voice_healthy: bool | None = None,
    active_subagents: int = 0,
    active_goals: int = 0,
    background_tasks: int = 0,
    commit: str = "unknown",
    mcp_discovery_status: str = "unknown",
) -> HealthStatus:
    components = {
        "llm": _component(
            _dependency_status(llm_healthy),
            provider=llm_provider,
        ),
        "sqlite": _sqlite_health(repository),
        "milvus": await _milvus_health(vector_index),
        "rag_backlog": _backlog_health(repository),
        "transcripts": _transcript_health(Path(transcript_dir)),
        "mcp_discovery": _component(mcp_discovery_status),
        "voice": _component(
            _dependency_status(voice_healthy),
            provider=voice_provider,
        ),
        "runtime_tasks": _component(
            "healthy",
            active_subagents=max(active_subagents, 0),
            active_goals=max(active_goals, 0),
            background_tasks=max(background_tasks, 0),
            subagent_recovery="unrecoverable",
            subagent_recovery_path=None,
        ),
    }
    return HealthStatus(
        schema_version="1",
        status=_overall_status(components),
        generated_at=datetime.now(UTC).isoformat(),
        version={"application": __version__, "commit": commit},
        runtime={"python": platform.python_version(), "implementation": sys.implementation.name},
        components=components,
    )


def default_status_doc_path() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "current-progress-status.md"


def _current_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            check=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if commit else "unknown"


def _status_memory_config() -> MemoryRagConfig:
    config = MemoryRagConfig.from_env()
    database_path = os.getenv("REASONING_MEMORY_DB", "").strip()
    if not database_path:
        return config
    return MemoryRagConfig(
        database_path=Path(database_path),
        enabled=config.enabled,
        retrieval_limit=config.retrieval_limit,
        prompt_char_budget=config.prompt_char_budget,
        embedding=config.embedding,
        milvus=config.milvus,
        extraction_mode=config.extraction_mode,
        extraction=config.extraction,
    )


def _status_transcript_dir() -> Path:
    transcript_dir = os.getenv("REASONING_TRANSCRIPT_DIR", "").strip()
    if transcript_dir:
        return Path(transcript_dir)
    return get_transcript_dir()


async def build_default_health_status() -> HealthStatus:
    config = _status_memory_config()
    repository = MemoryRepository(config.database_path)
    return await build_health_status(
        repository=repository,
        vector_index=None,
        transcript_dir=_status_transcript_dir(),
        commit=_current_commit(),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="reasoning-status-sync",
        description="Render or sync the engineering progress dashboard markdown.",
    )
    parser.add_argument(
        "--write-doc",
        action="store_true",
        help="Write the rendered markdown back to docs/current-progress-status.md",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Override the rendered update date (default: today).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable health status JSON.",
    )
    args = parser.parse_args(argv)

    if args.json:
        health = asyncio.run(build_default_health_status())
        print(json.dumps(health.to_dict(), sort_keys=True))
        return

    if args.write_doc:
        write_status_markdown(default_status_doc_path(), updated_at=args.date)
        return

    print(render_status_markdown(updated_at=args.date))


if __name__ == "__main__":
    main()
