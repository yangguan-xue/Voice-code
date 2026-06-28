"""子 agent transcript 路径辅助。"""

from __future__ import annotations

from pathlib import Path


def get_subagent_transcript_path(transcript_root: Path, session_id: str, task_id: str) -> Path:
    path = transcript_root / f"{session_id}-subagents" / f"{task_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
