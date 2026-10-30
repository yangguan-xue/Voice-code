"""Command-line entry point for the web demo bridge server (tenant model)."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from voice_code.llm.models import _load_dotenv
from voice_code.web_demo.limits import WebDemoLimits
from voice_code.web_demo.runtime import TenantSessionManager
from voice_code.web_demo.tenants import TenantStore


def main(argv: list[str] | None = None) -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(
        description="Run the Voice Code web demo sandbox agent server (tenant model).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--access-codes",
        default=os.getenv("REASONING_WEB_DEMO_ACCESS_CODES", ""),
        help='Tenant access codes csv: "alice=Alice,bob=Bob"（key 即 tenant_id/访问码）.',
    )
    parser.add_argument("--sandbox-root", required=True)
    parser.add_argument(
        "--data-root",
        default=os.getenv("REASONING_WEB_DEMO_DATA_ROOT", ""),
        help="租户数据根目录（web-demo.db + transcripts）。默认 <sandbox-root>/_data",
    )
    parser.add_argument("--template-repo", default="")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        help="Allowed browser origin for the websocket server. Repeat for multiple origins.",
    )
    parser.add_argument(
        "--idle-timeout-seconds",
        type=float,
        default=30 * 60,
        help="热 runtime 空闲回收阈值（会话本身永不过期）.",
    )
    parser.add_argument("--max-runtimes", type=int, default=20)
    args = parser.parse_args(argv)

    if not args.access_codes:
        parser.error("--access-codes or REASONING_WEB_DEMO_ACCESS_CODES is required")

    asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> None:
    template_repo = Path(args.template_repo) if args.template_repo else None
    sandbox_root = Path(args.sandbox_root)
    data_root = Path(args.data_root) if args.data_root else sandbox_root / "_data"
    store = TenantStore.from_access_codes_csv(data_root / "web-demo.db", args.access_codes)
    manager = TenantSessionManager(
        tenant_store=store,
        sandbox_root=sandbox_root,
        data_root=data_root,
        template_repo=template_repo,
        profile=args.profile,
        model_name=args.model,
        limits=WebDemoLimits(
            runtime_idle_timeout_seconds=args.idle_timeout_seconds,
            max_active_runtimes=args.max_runtimes,
        ),
    )
    allowed_origins = [origin for origin in args.allowed_origin if origin]

    import uvicorn

    from voice_code.web_demo.app import create_app

    app = create_app(session_manager=manager, allowed_origins=allowed_origins)
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    asgi_server = uvicorn.Server(config)
    print(f"Web demo bridge (FastAPI, tenant model) listening on http://{args.host}:{args.port}")
    try:
        await asgi_server.serve()
    finally:
        await manager.shutdown()
        store.close()


if __name__ == "__main__":
    main()
