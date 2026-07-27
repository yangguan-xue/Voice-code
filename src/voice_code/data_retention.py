from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path


class DeleteSemantics(StrEnum):
    HARD_DELETE = "hard_delete"
    SOFT_DELETE = "soft_delete"
    ARCHIVE = "archive"


class DataCategory(StrEnum):
    TRANSCRIPT = "transcript"
    SESSION = "session"
    SUBAGENT = "subagent"
    GOAL = "goal"
    RAG = "rag"
    WORKTREE = "worktree"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    category: DataCategory
    owner: str
    retention_days: int
    delete_semantics: DeleteSemantics
    backup: bool

    def __post_init__(self) -> None:
        if not self.owner.strip():
            raise ValueError("Retention policy owner is required")
        if self.retention_days < 0:
            raise ValueError("Retention days must be non-negative")


@dataclass(frozen=True, slots=True)
class RetentionPolicyConfig:
    policies: dict[DataCategory, RetentionPolicy]

    @classmethod
    def default(cls) -> RetentionPolicyConfig:
        return cls(
            policies={
                DataCategory.TRANSCRIPT: RetentionPolicy(
                    DataCategory.TRANSCRIPT,
                    owner="local-user",
                    retention_days=90,
                    delete_semantics=DeleteSemantics.HARD_DELETE,
                    backup=True,
                ),
                DataCategory.SESSION: RetentionPolicy(
                    DataCategory.SESSION,
                    owner="local-user",
                    retention_days=90,
                    delete_semantics=DeleteSemantics.HARD_DELETE,
                    backup=True,
                ),
                DataCategory.SUBAGENT: RetentionPolicy(
                    DataCategory.SUBAGENT,
                    owner="local-user",
                    retention_days=30,
                    delete_semantics=DeleteSemantics.HARD_DELETE,
                    backup=True,
                ),
                DataCategory.GOAL: RetentionPolicy(
                    DataCategory.GOAL,
                    owner="local-user",
                    retention_days=180,
                    delete_semantics=DeleteSemantics.ARCHIVE,
                    backup=True,
                ),
                DataCategory.RAG: RetentionPolicy(
                    DataCategory.RAG,
                    owner="local-user",
                    retention_days=365,
                    delete_semantics=DeleteSemantics.SOFT_DELETE,
                    backup=True,
                ),
                DataCategory.WORKTREE: RetentionPolicy(
                    DataCategory.WORKTREE,
                    owner="local-user",
                    retention_days=7,
                    delete_semantics=DeleteSemantics.HARD_DELETE,
                    backup=False,
                ),
            }
        )

    @classmethod
    def from_toml(cls, path: Path) -> RetentionPolicyConfig:
        defaults = cls.default().policies
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        policies = dict(defaults)
        for category in DataCategory:
            section = payload.get(category.value, {})
            if not isinstance(section, dict):
                raise ValueError(f"Invalid retention policy section: {category.value}")
            current = defaults[category]
            policies[category] = RetentionPolicy(
                category=category,
                owner=str(section.get("owner", current.owner)),
                retention_days=int(section.get("retention_days", current.retention_days)),
                delete_semantics=DeleteSemantics(
                    str(section.get("delete_semantics", current.delete_semantics.value))
                ),
                backup=bool(section.get("backup", current.backup)),
            )
        return cls(policies=policies)

    def to_dict(self) -> dict[str, dict[str, object]]:
        return {
            category.value: {
                "owner": policy.owner,
                "retention_days": policy.retention_days,
                "delete_semantics": policy.delete_semantics.value,
                "backup": policy.backup,
            }
            for category, policy in self.policies.items()
        }


@dataclass(frozen=True, slots=True)
class CleanupResult:
    deleted_paths: list[Path]


def cleanup_expired_worktrees(root: Path, *, retention_days: int) -> CleanupResult:
    if retention_days < 0:
        raise ValueError("Retention days must be non-negative")
    if not root.exists():
        return CleanupResult(deleted_paths=[])
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted_paths: list[Path] = []
    for path in sorted(root.iterdir()):
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified_at >= cutoff:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        deleted_paths.append(path)
    return CleanupResult(deleted_paths=deleted_paths)
