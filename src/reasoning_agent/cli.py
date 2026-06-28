"""CLI 入口 — Phase 1 极简 REPL"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from langchain_core.messages import BaseMessage

from reasoning_agent.agent.abort import AbortSignal
from reasoning_agent.agent.loop import agent_loop
from reasoning_agent.agent.types import EventType
from reasoning_agent.commands import (
    COMMON_HELP,
    format_session_lines,
    parse_command,
    resume_session,
)
from reasoning_agent.llm.models import list_model_profiles
from reasoning_agent.permissions import PermissionContext
from reasoning_agent.runtime import bootstrap_runtime
from reasoning_agent.tui import main as tui_main

logger = logging.getLogger(__name__)


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
        "--plain", action="store_true",
        help="Force plain terminal mode instead of the interactive screen",
    )
    return p.parse_args(argv)


def _setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s [%(name)s] %(message)s",
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
    perm_ctx = PermissionContext(mode=args.permission_mode)
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
                print(f"Commands: {COMMON_HELP}")
                continue
            elif command.name == "sessions":
                for line in format_session_lines(limit=10):
                    print(line)
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
                print(f"Resumed session: {resumed.session_id}")
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
        ):
            _display_event(event)
        resume_messages = transcript_writer.read_all_messages()

        print()  # blank line after response


def main(argv: list[str] | None = None) -> None:
    try:
        args = parse_args(argv)
    except SystemExit:
        raise
    _setup_logging(args.debug)

    interactive_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive_tty and not args.plain and args.prompt is None:
        tui_argv: list[str] = []
        if args.profile:
            tui_argv.extend(["--profile", args.profile])
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
