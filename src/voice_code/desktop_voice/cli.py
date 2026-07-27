"""Command-line entry point for the desktop voice bridge server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence

from voice_code.desktop_voice.runtime import create_desktop_voice_runtime, runtime_payload
from voice_code.llm.models import _load_dotenv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reasoning-desktop-voice-bridge",
        description="Run the local Voice Code desktop voice WebSocket bridge.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", default=os.environ.get("REASONING_DESKTOP_VOICE_TOKEN"))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--stt-url", default="http://localhost:8765")
    parser.add_argument("--tts-url", default="http://localhost:8775")
    parser.add_argument("--stepfun-key", default=os.environ.get("STEPFUN_API_KEY", ""))
    parser.add_argument("--stepfun-voice", default="cixingnansheng")
    parser.add_argument(
        "--tts-enabled",
        action="store_true",
        help="Enable TTS playback on startup. Desktop defaults to muted.",
    )
    return parser


async def run(args: argparse.Namespace) -> None:
    bundle = await create_desktop_voice_runtime(
        profile=args.profile,
        workspace=args.workspace,
        token=args.token,
        stt_url=args.stt_url,
        tts_url=args.tts_url,
        stepfun_key=args.stepfun_key,
        stepfun_voice=args.stepfun_voice,
        tts_muted=not args.tts_enabled,
        api_key=os.environ.get("REASONING_DESKTOP_MODEL_API_KEY"),
        base_url=os.environ.get("REASONING_DESKTOP_MODEL_BASE_URL"),
        model_name=os.environ.get("REASONING_DESKTOP_MODEL_NAME"),
        custom_profile_id=os.environ.get("REASONING_DESKTOP_MODEL_CONFIG_ID"),
    )
    async with bundle.server.run(host=args.host, port=args.port) as running:
        print(
            json.dumps(runtime_payload(bundle, url=running.url), ensure_ascii=False),
            flush=True,
        )
        await asyncio.Event().wait()


def main(argv: Sequence[str] | None = None) -> None:
    _load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
