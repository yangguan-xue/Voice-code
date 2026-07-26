"""Discover small, file-backed agent skills without executing their contents."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_MAX_SKILL_BYTES = 256_000
_MAX_TOTAL_SKILL_BYTES = 512_000
_PROJECT_SKILL_DIRS = (".reasoning/skills", ".agents/skills", ".codex/skills")


@dataclass(frozen=True)
class SkillDocument:
    name: str
    path: Path
    content: str
    scope: str


def _skill_roots(workspace: Path) -> list[tuple[str, Path]]:
    roots = [("project", workspace / item) for item in _PROJECT_SKILL_DIRS]
    home = Path(os.environ.get("REASONING_HOME", Path.home() / ".reasoning"))
    roots.append(("user", home / "skills"))
    return roots


def discover_skills(workspace: str | Path) -> list[SkillDocument]:
    """Return project skills first, with project definitions overriding user ones."""
    resolved = Path(workspace).resolve()
    by_name: dict[str, SkillDocument] = {}
    total_bytes = 0
    for scope, root in reversed(_skill_roots(resolved)):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            try:
                resolved_path = path.resolve(strict=True)
                resolved_path.relative_to(root.resolve(strict=True))
                size = resolved_path.stat().st_size
                if size > _MAX_SKILL_BYTES or total_bytes + size > _MAX_TOTAL_SKILL_BYTES:
                    continue
                content = resolved_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError, ValueError):
                continue
            if content:
                total_bytes += size
                by_name[path.parent.name] = SkillDocument(
                    name=path.parent.name,
                    path=resolved_path,
                    content=content,
                    scope=scope,
                )
    return sorted(by_name.values(), key=lambda item: item.name)


def render_skills_prompt(skills: list[SkillDocument]) -> str:
    if not skills:
        return ""
    lines = [
        "# Available Skills",
        "",
        "Use a skill when the task matches it. Read and follow the full instructions below.",
    ]
    for skill in skills:
        lines.extend(
            [
                "",
                f"## Skill: {skill.name}",
                f"Source: {skill.path}",
                skill.content,
            ]
        )
    return "\n".join(lines)
