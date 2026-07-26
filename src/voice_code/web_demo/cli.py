"""Command-line entry point for the web demo bridge server."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from voice_code.llm.models import _load_dotenv
from voice_code.web_demo.limits import WebDemoLimits
from voice_code.web_demo.runtime import DemoSessionManager
from voice_code.web_demo.server import WebDemoServer


def main(argv: list[str] | None = None) -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(
        description="Run the Voice Code web demo sandbox agent server.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--access-code",
        default=os.getenv("REASONING_WEB_DEMO_ACCESS_CODE", ""),
        help="Invite code required by demo.session.create.",
    )
    parser.add_argument("--sandbox-root", required=True)
    parser.add_argument("--template-repo", default="")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        help="Allowed browser origin for the websocket server. Repeat for multiple origins.",
    )
    parser.add_argument("--ttl-seconds", type=int, default=30 * 60)
    parser.add_argument("--max-sessions", type=int, default=20)
    args = parser.parse_args(argv)

    if not args.access_code:
        parser.error("--access-code or REASONING_WEB_DEMO_ACCESS_CODE is required")

    asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> None:
    template_repo = Path(args.template_repo) if args.template_repo else None
    manager = DemoSessionManager(
        access_code=args.access_code,
        sandbox_root=Path(args.sandbox_root),
        template_repo=template_repo,
        profile=args.profile,
        model_name=args.model,
        limits=WebDemoLimits(
            session_ttl_seconds=args.ttl_seconds,
            max_active_sessions=args.max_sessions,
        ),
    )
    server = WebDemoServer(
        session_manager=manager,
        allowed_origins=[origin for origin in args.allowed_origin if origin],
    )
    async with server.run(host=args.host, port=args.port) as running:
        print(f"Web demo bridge listening on {running.url}")
        await asyncio.Future()


if __name__ == "__main__":
    main()
