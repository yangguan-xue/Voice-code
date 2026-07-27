"""Lightweight desktop metadata bootstrap without initializing the agent runtime."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path

from voice_code.session.manager import group_session_summaries, list_session_summaries

_ROOT_DIR = Path(__file__).resolve().parents[3]


def collect_desktop_metadata(workspace: str) -> dict[str, object]:
    resolved_workspace = str(Path(workspace).expanduser().resolve(strict=True))
    profiles, active_profile = _model_profiles()
    groups = group_session_summaries(list_session_summaries(limit=40))
    return {
        "sessionId": "",
        "sessionGroups": [
            {
                "id": group.key,
                "folderName": group.label,
                "workspacePath": group.project_path,
                "sessions": [
                    {
                        "id": session.id,
                        "title": session.title or "未命名任务",
                        "isActive": session.is_current,
                        "isEmpty": False,
                    }
                    for session in group.sessions
                ],
            }
            for group in groups
        ],
        "workspacePath": resolved_workspace,
        **_git_context(resolved_workspace),
        "activeProfile": active_profile,
        "modelProfiles": profiles,
    }


def _model_profiles() -> tuple[list[dict[str, str]], str]:
    models_file = _ROOT_DIR / "models.toml"
    data = tomllib.loads(models_file.read_text(encoding="utf-8")) if models_file.exists() else {}
    raw_profiles = data.get("profiles", {})
    profiles = raw_profiles if isinstance(raw_profiles, dict) else {}
    details = [
        {
            "id": name,
            "label": name,
            "modelName": str(value.get("model_name", name)),
        }
        for name, value in sorted(profiles.items())
        if isinstance(name, str) and isinstance(value, dict)
    ]
    active = os.getenv("LLM_PROFILE", "").strip() or _env_profile(_ROOT_DIR / ".env")
    return details, active


def _env_profile(path: Path) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "LLM_PROFILE":
            return value.strip().strip('"').strip("'")
    return ""


def _run_git(workspace: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", workspace, *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_context(workspace: str) -> dict[str, object]:
    branch = _run_git(workspace, "branch", "--show-current")
    raw_branches = _run_git(workspace, "branch", "--format=%(refname:short)")
    status = _run_git(workspace, "status", "--porcelain")
    return {
        "branch": branch,
        "branches": [value.strip() for value in raw_branches.splitlines() if value.strip()],
        "isDirty": bool(status),
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reasoning-desktop-metadata")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(collect_desktop_metadata(args.workspace), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
