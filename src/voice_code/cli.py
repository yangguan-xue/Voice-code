"""CLI 入口 — Phase 1 极简 REPL"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from voice_code import __version__
from voice_code.agent.abort import AbortSignal
from voice_code.agent.loop import agent_loop
from voice_code.agent.types import EventType
from voice_code.commands import (
    format_help_lines,
    format_session_lines,
    format_status_lines,
    parse_command,
    resume_session,
)
from voice_code.goal_prompt import GOAL_BUILDER_ADDENDUM
from voice_code.goals import (
    GoalBuildRequest,
    GoalExecutionAdapter,
    GoalRuntimeService,
    GoalSpec,
)
from voice_code.llm.models import list_model_profiles
from voice_code.memory.rag_models import MemoryScope as RagMemoryScope
from voice_code.memory.service import MemoryService, find_memory_file_path
from voice_code.permissions import (
    PermissionContext,
    clear_permission_rules,
    create_permission_rule,
    delete_permission_rule,
    describe_permission_rule,
    export_permission_state,
    load_session_rules_from_state,
    load_workspace_rules,
    permission_rule_add_usage,
    permission_rule_edit_usage,
    permission_rule_summary,
    update_permission_rule,
)
from voice_code.runtime import bootstrap_runtime, build_workspace_prompt
from voice_code.session import load_session_state, save_session_state
from voice_code.session.manager import make_session_id
from voice_code.telemetry import configure_logging
from voice_code.tui import main as tui_main

USER_AGENT = f"voice-code-cli/{__version__}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="reasoning",
        description="Interactive CLI coding agent",
    )
    p.add_argument(
        "prompt", nargs="?", default=None,
        help="Initial prompt (non-interactive mode if provided with --print)",
    )
    p.add_argument(
        "--model", default=None,
        help="Model name (default: from .env LLM_MODEL_NAME)",
    )
    p.add_argument(
        "--profile", default=None,
        help="Model profile name from models.toml",
    )
    p.add_argument(
        "--permission-mode",
        choices=["default", "acceptEdits", "bypassPermissions", "dontAsk"],
        default="default",
        help="Permission mode (default: default)",
    )
    p.add_argument(
        "--debug", action="store_true",
        help="Enable debug logging",
    )
    p.add_argument(
        "--log-format",
        choices=["console", "json"],
        default=None,
        help="Log format (default: REASONING_LOG_FORMAT or console)",
    )
    p.add_argument(
        "--plain", action="store_true",
        help="Force plain terminal mode instead of the interactive screen",
    )
    p.add_argument(
        "--version",
        action="store_true",
        dest="show_version",
        help="Print the voice-code package version and exit",
    )
    return p.parse_args(argv)


def _setup_logging(debug: bool, log_format: str | None = None) -> None:
    configure_logging(
        debug=debug,
        json_output=None if log_format is None else log_format == "json",
    )


def _display_event(event) -> None:
    """在终端展示 AgentEvent。"""
    if event.type == EventType.TEXT:
        text = event.content
        if isinstance(text, str):
            sys.stdout.write(text)
            sys.stdout.flush()
    elif event.type == EventType.REASONING:
        text = event.content
        if isinstance(text, str):
            sys.stdout.write(f"\x1b[2m{text}\x1b[0m")
            sys.stdout.flush()
    elif event.type == EventType.TOOL_CALL:
        args_str = ", ".join(
            f"{k}={repr(v)[:40]}" for k, v in event.tool_args.items()
        )
        print(f"\n⚡ {event.tool_name}({args_str})")
    elif event.type == EventType.TOOL_RESULT:
        summary = event.content[:100].replace("\n", " ")
        print(f"  ← {summary}")
    elif event.type == EventType.ERROR:
        print(f"\n✗ {event.content[:200]}")
    elif event.type == EventType.FINISH:
        pass


async def run_repl(args: argparse.Namespace) -> None:
    """启动 REPL 循环。"""
    runtime = await bootstrap_runtime(profile=args.profile, model_name=args.model)
    model = runtime.model
    fallback_model = runtime.fallback_model
    tools = runtime.tools
    cwd = runtime.cwd
    prompt = runtime.prompt
    perm_ctx = PermissionContext(
        mode=args.permission_mode,
        workspace_root=cwd,
        workspace_rules=load_workspace_rules(cwd),
    )
    state_result = load_session_state(runtime.session_id, fallback_cwd=cwd)
    perm_ctx.session_rules = load_session_rules_from_state(state_result.state.permission_state)
    current_state = state_result.state

    def _persist_permission_state(ctx: PermissionContext) -> None:
        nonlocal current_state, current_session_id
        current_state.session_id = current_session_id
        current_state.cwd = cwd
        current_state.permission_state = dict(export_permission_state(ctx))
        save_session_state(current_state)

    perm_ctx.on_change = _persist_permission_state
    abort_sig = AbortSignal()
    resume_messages: list[BaseMessage] | None = None
    current_session_id = runtime.session_id
    transcript_writer = runtime.transcript_writer

    print(f"\nreasoning agent — model: {model.model_name}")
    if args.profile:
        print(f"profile: {args.profile}")
    print(f"cwd: {cwd}")
    print(f"session: {current_session_id}")
    print(f"tools: {', '.join(t.name for t in tools)}")
    print(f"permission mode: {args.permission_mode}")
    print("Type /help for help, /exit or Ctrl+D to quit.\n")

    # ---- REPL ----
    while True:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            user_input = input("> ")
        except EOFError:
            print("\nGoodbye.")
            break
        except KeyboardInterrupt:
            print("\n^C")
            continue
        finally:
            signal.signal(signal.SIGINT, lambda s, f: abort_sig.trigger())

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle slash commands
        if user_input.startswith("/"):
            command = parse_command(user_input)
            if command.name == "quit":
                transcript_writer.close()
                print("Goodbye.")
                break
            elif command.name == "help":
                for line in format_help_lines():
                    print(line)
                continue
            elif command.name == "sessions":
                for line in format_session_lines(limit=10):
                    print(line)
                continue
            elif command.name == "status":
                for line in format_status_lines("overview"):
                    print(line)
                continue
            elif command.name == "status_areas":
                for line in format_status_lines("areas"):
                    print(line)
                continue
            elif command.name == "status_gaps":
                for line in format_status_lines("gaps"):
                    print(line)
                continue
            elif command.name == "goal_status":
                goal_id = str(command.args.get("goal_id", "")).strip()
                if not goal_id:
                    print("Usage: /goal-status <id>")
                    continue
                try:
                    state = GoalRuntimeService(cwd).inspect(goal_id)
                    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
                except (FileNotFoundError, ValueError) as exc:
                    print(f"Unable to load goal: {exc}")
                continue
            elif command.name == "goal_stop":
                goal_id = str(command.args.get("goal_id", "")).strip()
                if not goal_id:
                    print("Usage: /goal-stop <id>")
                    continue
                try:
                    GoalRuntimeService(cwd).request_stop(goal_id)
                    print(f"Stop requested for goal: {goal_id}")
                except (FileNotFoundError, ValueError) as exc:
                    print(f"Unable to stop goal: {exc}")
                continue
            elif command.name in {"goal", "goal_resume"}:
                is_resume = command.name == "goal_resume"
                if is_resume:
                    goal_id = str(command.args.get("goal_id", "")).strip()
                    if not goal_id:
                        print("Usage: /goal-resume <id>")
                        continue
                    try:
                        spec = GoalRuntimeService(cwd).load_spec(goal_id)
                    except (FileNotFoundError, ValueError) as exc:
                        print(f"Unable to resume goal: {exc}")
                        continue
                else:
                    objective = str(command.args.get("objective", "")).strip()
                    verification_commands = command.args.get("verification_commands", [])
                    allowed_paths = command.args.get("allowed_paths", [])
                    if (
                        not objective
                        or not isinstance(verification_commands, list)
                        or not verification_commands
                        or not isinstance(allowed_paths, list)
                        or not allowed_paths
                    ):
                        print(
                            "Usage: /goal --allow <path> --verify <command> "
                            "[--max-iterations N] <objective>"
                        )
                        continue
                    goal_id = make_session_id().lower()
                    spec = GoalSpec(
                        goal_id=goal_id,
                        objective=objective,
                        workspace=cwd,
                        verification_commands=[str(item) for item in verification_commands],
                        allowed_paths=[str(item) for item in allowed_paths],
                        max_iterations=int(command.args.get("max_iterations", 5)),
                    )
                goal_system_prompt = ""
                goal_permission_context: PermissionContext | None = None

                async def _goal_builder(request: GoalBuildRequest) -> str:
                    nonlocal goal_system_prompt, goal_permission_context
                    chunks: list[str] = []
                    abort_sig.clear()
                    if not goal_system_prompt:
                        goal_system_prompt = await build_workspace_prompt(
                            workspace=request.workspace,
                            tools=tools,
                            model_name=model.model_name,
                            use_cache=False,
                        )
                        goal_system_prompt = goal_system_prompt + GOAL_BUILDER_ADDENDUM
                    if goal_permission_context is None:
                        goal_permission_context = PermissionContext(
                            mode="bypassPermissions",
                            approver=perm_ctx.approver,
                            workspace_root=request.workspace,
                            workspace_rules=load_workspace_rules(request.workspace),
                        )
                    async for goal_event in agent_loop(
                        user_input=request.prompt,
                        tools=tools,
                        system_prompt=goal_system_prompt,
                        model=model,
                        permission_context=goal_permission_context,
                        abort_signal=abort_sig,
                        fallback_model=fallback_model,
                        runtime_session_id=f"goal-{goal_id}",
                    ):
                        _display_event(goal_event)
                        if goal_event.type == EventType.TEXT:
                            chunks.append(str(goal_event.content))
                    return "".join(chunks).strip()

                async def _goal_reviewer(review_spec: GoalSpec, result: str) -> list[str]:
                    review_prompt = (
                        "Review the result against the objective. Return a JSON array of blocking "
                        "issues only; return [] when no blocker remains.\n"
                        "You are a JUDGE, not an executor. You cannot run commands, read "
                        "files, or modify the workspace, and you must NOT list your own "
                        "lack of filesystem access as a blocker. Judge ONLY from the "
                        "Objective and the Result text provided below: whether the result "
                        "actually addresses the objective, "
                        "whether the claimed verification commands were actually run and shown, "
                        "and whether the acceptance items were actually updated.\n"
                        f"Objective: {review_spec.objective}\nResult: {result[-12000:]}"
                    )
                    response = await model.ainvoke(
                        [
                            SystemMessage(
                                content=(
                                    "You are an independent, strict code reviewer. "
                                    "You review text evidence only; you have no tools and no "
                                    "filesystem access. Never complain about your own lack of "
                                    "tools or access — only judge the evidence shown to you."
                                )
                            ),
                            HumanMessage(content=review_prompt),
                        ]
                    )
                    raw = str(response.content or "").strip()
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        return [raw] if raw else []
                    return [str(item) for item in parsed] if isinstance(parsed, list) else [raw]

                service = GoalRuntimeService(
                    cwd,
                    adapter_factory=lambda _spec: GoalExecutionAdapter(
                        builder=_goal_builder,
                        reviewer=_goal_reviewer,
                    ),
                )
                print(f"Goal {'resumed' if is_resume else 'started'}: {goal_id}")
                goal_result = (
                    await service.resume(goal_id)
                    if is_resume
                    else await service.start(spec)
                )
                print(
                    f"\nGoal {goal_id}: {goal_result.state.status} — {goal_result.message}"
                )
                continue
            elif command.name == "memory":
                memory_service = MemoryService(project_root=cwd)
                memory_rag = runtime.memory_service
                action = str(command.args.get("action", "list"))
                entry_id = str(command.args.get("entry_id", "")).strip()
                if action == "list":
                    entries = (
                        memory_rag.list(user_id="local")
                        if memory_rag is not None
                        else memory_service.list()
                    )
                    if not entries:
                        print("No memories.")
                    for entry in entries:
                        label = getattr(entry, "name", getattr(entry, "summary", ""))
                        print(f"{entry.id} [{entry.scope}] {label}")
                elif action == "show":
                    rag_entry = memory_rag.get(entry_id, user_id="local") if memory_rag else None
                    path = find_memory_file_path(entry_id, cwd) if rag_entry is None else None
                    output = rag_entry.content if rag_entry else (
                        path.read_text(encoding="utf-8")
                        if path
                        else f"Memory not found: {entry_id}"
                    )
                    print(output)
                elif action == "candidates" and memory_rag is not None:
                    candidates = memory_rag.list_candidates(user_id="local")
                    if not candidates:
                        print("No memory candidates.")
                    for item in candidates:
                        print(
                            f"{item.candidate.candidate_id} [{item.status}] "
                            f"{item.candidate.content}"
                        )
                elif action == "approve" and memory_rag is not None:
                    try:
                        entry = memory_rag.approve_candidate(entry_id, user_id="local")
                        print(f"Approved: {entry.id}")
                    except ValueError as exc:
                        print(f"Memory candidate not approved: {exc}")
                elif action == "reject" and memory_rag is not None:
                    rejected = memory_rag.reject_candidate(entry_id, user_id="local")
                    print(
                        f"Rejected: {entry_id}"
                        if rejected
                        else f"Memory candidate not found: {entry_id}"
                    )
                elif action == "conflicts" and memory_rag is not None:
                    conflicts = memory_rag.list_conflicts(user_id="local")
                    if not conflicts:
                        print("No memory conflicts.")
                    for item in conflicts:
                        print(f"{item.candidate.candidate_id} {item.candidate.content}")
                elif action == "resolve" and memory_rag is not None:
                    keep_id = str(command.args.get("keep_id", "")).strip()
                    try:
                        entry = memory_rag.resolve_conflict(
                            entry_id,
                            keep_memory_id=keep_id,
                            user_id="local",
                        )
                        print(f"Conflict resolved; kept: {entry.id}")
                    except ValueError as exc:
                        print(f"Conflict not resolved: {exc}")
                elif action == "why" and memory_rag is not None:
                    try:
                        print(json.dumps(
                            memory_rag.explain_memory(entry_id, user_id="local"),
                            ensure_ascii=False,
                            indent=2,
                        ))
                    except ValueError as exc:
                        print(str(exc))
                elif action == "edit" and memory_rag is not None:
                    replacement = str(command.args.get("text", "")).strip()
                    try:
                        entry = memory_rag.edit_memory(
                            entry_id, replacement, user_id="local"
                        )
                        print(f"Updated: {entry.id} v{entry.version}")
                    except ValueError as exc:
                        print(f"Memory not updated: {exc}")
                elif action == "reindex":
                    if memory_rag is not None:
                        delivered = await memory_rag.reindex()
                        print(f"Memory index rebuilt: {delivered} event(s).")
                        continue
                    memory_service.reindex()
                    print("Memory index rebuilt.")
                elif action == "audit":
                    issues = memory_service.audit()
                    print("No memory issues." if not issues else "\n".join(
                        f"[{item['type']}] {item['message']}" for item in issues
                    ))
                else:
                    print(
                        "Usage: /memory [list|show <id>|candidates|approve <id>|"
                        "reject <id>|conflicts|resolve <id> --keep <memory_id>|"
                        "why <id>|edit <id> <text>|reindex|audit]"
                    )
                continue
            elif command.name == "remember":
                text = str(command.args.get("text", "")).strip()
                scope = str(command.args.get("scope", "project"))
                if not text:
                    print("Usage: /remember [user|project] <text>")
                else:
                    try:
                        if runtime.memory_service is not None:
                            result = runtime.memory_service.remember(
                                text,
                                user_id="local",
                                project_key=runtime.memory_project_key,
                                scope=RagMemoryScope(scope),
                                session_id=current_session_id,
                            )
                            print(f"Remembered: {result.entry.id} [{result.entry.scope}]")
                            await runtime.memory_service.sync_pending(limit=20)
                        else:
                            entry = MemoryService(project_root=cwd).remember(
                                text, session_id=current_session_id, scope=scope
                            )
                            print(f"Remembered: {entry.id} [{entry.scope}]")
                    except ValueError as exc:
                        print(f"Memory not saved: {exc}")
                continue
            elif command.name == "forget":
                entry_id = str(command.args.get("entry_id", "")).strip()
                scope = str(command.args.get("scope", "project"))
                if not entry_id:
                    print("Usage: /forget [user|project] <id>")
                else:
                    forgotten = (
                        runtime.memory_service.archive(entry_id, user_id="local")
                        if runtime.memory_service is not None
                        else MemoryService(project_root=cwd).forget(entry_id, scope)
                    )
                    print(f"Archived: {entry_id}" if forgotten else f"Memory not found: {entry_id}")
                continue
            elif command.name == "resume":
                session_id = str(command.args.get("session_id", "")).strip()
                if not session_id:
                    print("Usage: /resume <id>")
                    continue
                try:
                    resumed = resume_session(session_id)
                except FileNotFoundError:
                    print(f"Session not found: {session_id}")
                    continue
                resume_messages = resumed.messages
                transcript_writer.close()
                current_session_id = resumed.session_id
                transcript_writer = resumed.transcript_writer
                current_state = resumed.runtime_state
                perm_ctx.session_id = resumed.session_id
                perm_ctx.session_rules = load_session_rules_from_state(
                    resumed.runtime_state.permission_state
                )
                print(f"Resumed session: {resumed.session_id}")
                continue
            elif command.name == "perm":
                for line in permission_rule_summary(perm_ctx):
                    print(line)
                continue
            elif command.name == "perm_rules":
                for line in permission_rule_summary(perm_ctx):
                    print(line)
                continue
            elif command.name == "perm_clear":
                scope = str(command.args.get("scope", "session")).strip().lower()
                if scope not in {"session", "workspace", "all"}:
                    print("Usage: /perm-clear [session|workspace|all]")
                    continue
                removed_session, removed_workspace = clear_permission_rules(
                    perm_ctx,
                    scope=scope,
                )
                print(
                    "Permission rules cleared:"
                    f" session={removed_session} workspace={removed_workspace}"
                )
                continue
            elif command.name == "perm_add":
                scope = str(command.args.get("scope", "session")).strip().lower()
                behavior = str(command.args.get("behavior", "")).strip().lower()
                updates = command.args.get("updates", {})
                if (
                    scope not in {"session", "workspace"}
                    or behavior not in {"allow", "deny", "ask"}
                    or not isinstance(updates, dict)
                    or not updates
                ):
                    print(permission_rule_add_usage())
                    continue
                try:
                    created = create_permission_rule(
                        perm_ctx,
                        scope=scope,
                        behavior=behavior,
                        updates=updates,
                    )
                    rule_count = (
                        len(perm_ctx.session_rules)
                        if scope == "session"
                        else len(perm_ctx.workspace_rules)
                    )
                    print(
                        f"Added {scope} rule {rule_count}: {created.name}"
                    )
                except ValueError as exc:
                    print(str(exc))
                continue
            elif command.name == "perm_rule":
                scope = str(command.args.get("scope", "session")).strip().lower()
                index = command.args.get("index")
                if scope not in {"session", "workspace"} or not isinstance(index, int):
                    print("Usage: /perm-rule [session|workspace] <index>")
                    continue
                try:
                    for line in describe_permission_rule(perm_ctx, scope=scope, index=index):
                        print(line)
                except (IndexError, ValueError) as exc:
                    print(str(exc))
                continue
            elif command.name == "perm_delete":
                scope = str(command.args.get("scope", "session")).strip().lower()
                index = command.args.get("index")
                if scope not in {"session", "workspace"} or not isinstance(index, int):
                    print("Usage: /perm-delete [session|workspace] <index>")
                    continue
                try:
                    removed = delete_permission_rule(perm_ctx, scope=scope, index=index)
                    print(
                        f"Deleted {scope} rule {index}: {removed.name}"
                    )
                except (IndexError, ValueError) as exc:
                    print(str(exc))
                continue
            elif command.name == "perm_edit":
                scope = str(command.args.get("scope", "session")).strip().lower()
                index = command.args.get("index")
                behavior = str(command.args.get("behavior", "")).strip().lower()
                reason_message = str(command.args.get("reason_message", "")).strip()
                updates = command.args.get("updates", {})
                if (
                    scope not in {"session", "workspace"}
                    or not isinstance(index, int)
                    or (
                        behavior not in {"allow", "deny", "ask"}
                        and not isinstance(updates, dict)
                    )
                    or (not behavior and not updates)
                ):
                    print(permission_rule_edit_usage())
                    continue
                try:
                    updated = update_permission_rule(
                        perm_ctx,
                        scope=scope,
                        index=index,
                        behavior=behavior,
                        reason_message=reason_message or None,
                        updates=updates if isinstance(updates, dict) else None,
                    )
                    print(
                        f"Updated {scope} rule {index}: {updated.name}"
                        f" -> {updated.behavior.name.lower()}"
                    )
                except (IndexError, ValueError) as exc:
                    print(str(exc))
                continue
            else:
                print(f"Unknown command: {user_input}")
                continue

        abort_sig.clear()
        async for event in agent_loop(
            user_input=user_input,
            tools=tools,
            system_prompt=prompt,
            model=model,
            permission_context=perm_ctx,
            abort_signal=abort_sig,
            resume_messages=resume_messages,
            transcript_writer=transcript_writer,
            fallback_model=fallback_model,
            memory_service=runtime.memory_service,
            memory_project_key=runtime.memory_project_key,
        ):
            _display_event(event)
        resume_messages = transcript_writer.read_all_messages()

        print()  # blank line after response


def main(argv: list[str] | None = None) -> None:
    try:
        args = parse_args(argv)
    except SystemExit:
        raise
    if args.show_version:
        print(__version__)
        return
    _setup_logging(args.debug, args.log_format)

    interactive_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive_tty and not args.plain and args.prompt is None:
        tui_argv: list[str] = []
        if args.profile:
            tui_argv.extend(["--profile", args.profile])
        if args.debug:
            tui_argv.append("--debug")
        if args.log_format:
            tui_argv.extend(["--log-format", args.log_format])
        try:
            tui_main(tui_argv)
            return
        except ValueError as e:
            profiles = ", ".join(list_model_profiles()) or "(none)"
            print(f"Error: {e}", file=sys.stderr)
            print(f"Available profiles: {profiles}", file=sys.stderr)
            sys.exit(2)

    try:
        asyncio.run(run_repl(args))
    except ValueError as e:
        profiles = ", ".join(list_model_profiles()) or "(none)"
        print(f"Error: {e}", file=sys.stderr)
        print(f"Available profiles: {profiles}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
