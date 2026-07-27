"""Command-line entry point for the desktop bridge server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence

from voice_code.desktop.runtime import create_desktop_bridge_runtime, runtime_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reasoning-desktop-bridge",
        description="Run the local Voice Code desktop WebSocket bridge.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.environ.get("REASONING_DESKTOP_BRIDGE_TOKEN"))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--workspace", default=None)
    return parser


async def run(args: argparse.Namespace) -> None:
    bundle = await create_desktop_bridge_runtime(
        profile=args.profile,
        model_name=os.environ.get("REASONING_DESKTOP_MODEL_NAME") or args.model,
        api_key=os.environ.get("REASONING_DESKTOP_MODEL_API_KEY"),
        base_url=os.environ.get("REASONING_DESKTOP_MODEL_BASE_URL"),
        custom_profile_id=os.environ.get("REASONING_DESKTOP_MODEL_CONFIG_ID"),
        workspace=args.workspace,
        token=args.token,
    )
    async with bundle.server.run(host=args.host, port=args.port) as running:
        print(
            json.dumps(runtime_payload(bundle, url=running.url), ensure_ascii=False),
            flush=True,
        )
        await asyncio.Event().wait()


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
