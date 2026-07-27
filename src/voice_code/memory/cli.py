"""Standalone memory maintenance commands."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from voice_code import __version__
from voice_code.memory.bootstrap import build_memory_rag_service
from voice_code.memory.consolidation import MemoryConsolidator
from voice_code.memory.evaluation import (
    EvaluationCase,
    evaluate_predictions,
    write_gate_report,
)
from voice_code.memory.extraction import ExtractionError, TurnExtractionInput
from voice_code.memory.migration_v1 import migrate_v1
from voice_code.memory.paths import (
    get_memory_root,
    get_user_memory_dir,
)
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.service import MemoryService
from voice_code.telemetry import configure_logging

USER_AGENT = f"voice-code-memory/{__version__}"


def print_paths() -> None:
    print(f"Memory root: {get_memory_root()}")
    print(f"User memory dir: {get_user_memory_dir()}")


def run_reindex(project_root: str | None = None) -> None:
    service = MemoryService(project_root=project_root)
    service.reindex()
    print("Index rebuilt.")


def run_audit(project_root: str | None = None) -> None:
    service = MemoryService(project_root=project_root)
    issues = service.audit()
    if not issues:
        print("No issues found.")
        return
    for issue in issues:
        print(f"[{issue['type']}] {issue['message']}")


def run_migrate(project_root: str | None, *, apply: bool) -> None:
    service = build_memory_rag_service()
    if service is None:
        raise SystemExit("Persistent memory is disabled (REASONING_MEMORY_ENABLED=false).")
    result = migrate_v1(
        service.repository,
        project_root=str(Path(project_root).resolve()) if project_root else None,
        dry_run=not apply,
    )
    mode = "applied" if apply else "dry-run"
    print(
        f"Migration {mode}: discovered={result.discovered}, imported={result.imported}, "
        f"duplicates={result.duplicates}, invalid={result.invalid}."
    )
    if not apply and result.discovered:
        print("Run again with --apply to import these memories.")


async def run_sync() -> None:
    service = build_memory_rag_service()
    if service is None:
        raise SystemExit("Persistent memory is disabled (REASONING_MEMORY_ENABLED=false).")
    if service.vector_index is None:
        raise SystemExit("Embedding and Milvus configuration are both required for sync.")
    delivered = 0
    for _ in range(100):
        batch = await service.sync_pending(limit=100)
        delivered += batch
        if batch < 100:
            break
    print(f"Memory index synchronized: {delivered} event(s).")


async def run_rebuild() -> None:
    service = build_memory_rag_service()
    if service is None or service.vector_index is None:
        raise SystemExit("Embedding and Milvus configuration are both required for rebuild.")
    delivered = await service.rebuild_index()
    print(f"Memory index rebuilt: {delivered} event(s).")


def run_dlq_list() -> None:
    service = build_memory_rag_service()
    if service is None:
        raise SystemExit("Persistent memory is disabled (REASONING_MEMORY_ENABLED=false).")
    print(json.dumps(service.repository.list_dead_letters(), ensure_ascii=False, sort_keys=True))


def run_dlq_explain(dead_letter_id: str) -> None:
    service = build_memory_rag_service()
    if service is None:
        raise SystemExit("Persistent memory is disabled (REASONING_MEMORY_ENABLED=false).")
    print(
        json.dumps(
            service.repository.explain_dead_letter(dead_letter_id),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def run_dlq_replay(dead_letter_id: str) -> None:
    service = build_memory_rag_service()
    if service is None:
        raise SystemExit("Persistent memory is disabled (REASONING_MEMORY_ENABLED=false).")
    replayed = service.repository.replay_dead_letter(dead_letter_id)
    print(json.dumps({"dead_letter_id": dead_letter_id, "replayed": replayed}, sort_keys=True))


async def run_health() -> None:
    service = build_memory_rag_service()
    if service is None:
        raise SystemExit("Persistent memory is disabled (REASONING_MEMORY_ENABLED=false).")
    if service.vector_index is None:
        raise SystemExit("SQLite ready; vector retrieval is not configured.")
    healthy = await service.vector_index.health()
    if not healthy:
        raise SystemExit("SQLite ready; Milvus is unavailable or collection is not initialized.")
    print("SQLite and Milvus are ready.")


def run_consolidate(*, apply: bool, user_id: str = "local") -> None:
    service = build_memory_rag_service()
    if service is None:
        raise SystemExit("Persistent memory is disabled (REASONING_MEMORY_ENABLED=false).")
    consolidator = MemoryConsolidator(service.repository)
    proposals = consolidator.plan(user_id=user_id)
    if not apply:
        for proposal in proposals:
            print(
                f"merge {proposal.duplicate_id} -> {proposal.keeper_id} "
                f"similarity={proposal.similarity:.3f}"
            )
        print(f"Consolidation dry-run: {len(proposals)} proposal(s).")
        return
    applied = consolidator.apply(proposals, user_id=user_id)
    print(f"Consolidation applied: {applied} merge(s).")


async def run_extract_eval(
    dataset: str,
    *,
    gate_report: str | None,
    reviewed_turns: int,
    observation_days: int,
) -> None:
    service = build_memory_rag_service()
    if service is None or service.extraction_worker is None:
        raise SystemExit("A shadow/review extraction provider configuration is required.")
    cases: list[EvaluationCase] = []
    predictions = []
    for line_number, line in enumerate(Path(dataset).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            case = EvaluationCase(
                case_id=str(item["case_id"]),
                eligible=bool(item["eligible"]),
                category=str(item["category"]),
                expected_kind=(
                    MemoryKind(str(item["expected_kind"]))
                    if item.get("expected_kind")
                    else None
                ),
                expected_scope=(
                    MemoryScope(str(item["expected_scope"]))
                    if item.get("expected_scope")
                    else None
                ),
            )
            turn = TurnExtractionInput(
                user_text=str(item["user_text"]),
                assistant_text=str(item.get("assistant_text", "")),
                source_session_id=f"eval-session-{line_number}",
                source_turn_id=f"eval-turn-{line_number}",
                project_available=bool(item.get("project_available", False)),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Invalid evaluation dataset line {line_number}") from exc
        cases.append(case)
        try:
            predictions.append(await service.extraction_worker.provider.extract(turn))
        except ExtractionError:
            predictions.append([])
    report = evaluate_predictions(cases, predictions)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    if gate_report:
        write_gate_report(
            Path(gate_report),
            report,
            reviewed_turns=reviewed_turns,
            observation_days=observation_days,
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reasoning-memory")
    parser.add_argument(
        "command",
        choices=[
            "paths",
            "reindex",
            "audit",
            "migrate",
            "sync",
            "rebuild",
            "health",
            "consolidate",
            "extract-eval",
            "dlq-list",
            "dlq-explain",
            "dlq-replay",
        ],
    )
    parser.add_argument("--project-root", default=None, help="Project root path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply V1 migration; migrate is a dry-run without this flag",
    )
    parser.add_argument("--dataset", default="", help="JSONL extraction evaluation dataset")
    parser.add_argument("--gate-report", default=None, help="Write a rollout gate report")
    parser.add_argument("--reviewed-turns", type=int, default=0)
    parser.add_argument("--observation-days", type=int, default=0)
    parser.add_argument(
        "--dead-letter-id",
        default="",
        help="Dead-letter ID for dlq-explain/replay",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--log-format",
        choices=["console", "json"],
        default=None,
        help="Log format (default: REASONING_LOG_FORMAT or console)",
    )
    args = parser.parse_args(argv)
    configure_logging(
        debug=args.debug,
        json_output=None if args.log_format is None else args.log_format == "json",
    )

    if args.command == "paths":
        print_paths()
    elif args.command == "reindex":
        run_reindex(args.project_root)
    elif args.command == "audit":
        run_audit(args.project_root)
    elif args.command == "migrate":
        run_migrate(args.project_root, apply=args.apply)
    elif args.command == "sync":
        asyncio.run(run_sync())
    elif args.command == "rebuild":
        asyncio.run(run_rebuild())
    elif args.command == "health":
        asyncio.run(run_health())
    elif args.command == "consolidate":
        run_consolidate(apply=args.apply)
    elif args.command == "extract-eval":
        if not args.dataset:
            raise SystemExit("extract-eval requires --dataset")
        asyncio.run(
            run_extract_eval(
                args.dataset,
                gate_report=args.gate_report,
                reviewed_turns=args.reviewed_turns,
                observation_days=args.observation_days,
            )
        )
    elif args.command == "dlq-list":
        run_dlq_list()
    elif args.command == "dlq-explain":
        if not args.dead_letter_id:
            raise SystemExit("dlq-explain requires --dead-letter-id")
        run_dlq_explain(args.dead_letter_id)
    elif args.command == "dlq-replay":
        if not args.dead_letter_id:
            raise SystemExit("dlq-replay requires --dead-letter-id")
        run_dlq_replay(args.dead_letter_id)


if __name__ == "__main__":
    main()
