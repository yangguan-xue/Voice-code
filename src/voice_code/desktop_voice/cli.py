"""Command-line entry point for the desktop voice bridge server."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence

from voice_code.desktop.startup import serialize_startup_json
from voice_code.desktop_voice.runtime import (
    DEFAULT_REMOTE_STT_URL,
    DEFAULT_REMOTE_TTS_URL,
    create_desktop_voice_runtime,
    runtime_payload,
)
from voice_code.llm.models import _load_dotenv
from voice_code.voice.stepfun_client import DEFAULT_VOICE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-code-desktop-voice-bridge",
        description="Run the local Voice Code desktop voice WebSocket bridge.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", default=os.environ.get("REASONING_DESKTOP_VOICE_TOKEN"))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument(
        "--voice-provider",
        default=os.environ.get("REASONING_DESKTOP_VOICE_PROVIDER", ""),
    )
    parser.add_argument(
        "--stt-url",
        default=os.environ.get("REASONING_DESKTOP_STT_URL", DEFAULT_REMOTE_STT_URL),
    )
    parser.add_argument(
        "--tts-url",
        default=os.environ.get("REASONING_DESKTOP_TTS_URL", DEFAULT_REMOTE_TTS_URL),
    )
    parser.add_argument(
        "--stepfun-key",
        default=(
            os.environ.get("REASONING_DESKTOP_STEPFUN_API_KEY")
            or os.environ.get("STEPFUN_API_KEY", "")
        ),
    )
    parser.add_argument(
        "--stepfun-voice",
        default=os.environ.get("REASONING_DESKTOP_STEPFUN_VOICE", DEFAULT_VOICE),
    )
    parser.add_argument(
        "--stepfun-base-url",
        default=os.environ.get("REASONING_DESKTOP_STEPFUN_BASE_URL", ""),
    )
    parser.add_argument(
        "--voice-api-key",
        default=os.environ.get("REASONING_DESKTOP_VOICE_API_KEY", ""),
    )
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
        voice_provider=args.voice_provider,
        stt_url=args.stt_url,
        tts_url=args.tts_url,
        stepfun_key=args.stepfun_key,
        stepfun_voice=args.stepfun_voice,
        stepfun_base_url=args.stepfun_base_url,
        voice_api_key=args.voice_api_key,
        tts_muted=not args.tts_enabled,
        api_key=os.environ.get("REASONING_DESKTOP_MODEL_API_KEY"),
        base_url=os.environ.get("REASONING_DESKTOP_MODEL_BASE_URL"),
        model_name=os.environ.get("REASONING_DESKTOP_MODEL_NAME"),
        custom_profile_id=os.environ.get("REASONING_DESKTOP_MODEL_CONFIG_ID"),
    )
    async with bundle.server.run(host=args.host, port=args.port) as running:
        print(
            serialize_startup_json(runtime_payload(bundle, url=running.url)),
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
