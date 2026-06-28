"""Transcript session manager."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path

from voice_code.session.transcript import TranscriptReader


def get_transcript_dir() -> Path:
    return Path.home() / ".reasoning" / "transcripts"


def make_session_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    return f"{timestamp}-{suffix}"


def get_session_path(session_id: str) -> Path:
    return get_transcript_dir() / f"{session_id}.jsonl"


def list_sessions(limit: int = 20) -> list[dict[str, str | int]]:
    transcript_dir = get_transcript_dir()
    if not transcript_dir.exists():
        return []

    files = sorted(transcript_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    results: list[dict[str, str | int]] = []
    for path in files[:limit]:
        reader = TranscriptReader(path)
        info = reader.read_info()
        results.append({
            "id": path.stem,
            "title": str(info["title"]),
            "message_count": int(info["message_count"]),
            "created_at": datetime.fromtimestamp(path.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        })
    return results
