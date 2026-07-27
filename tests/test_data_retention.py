from __future__ import annotations

from pathlib import Path

from voice_code.data_retention import (
    DataCategory,
    DeleteSemantics,
    RetentionPolicyConfig,
)


def test_default_retention_policy_declares_all_required_categories():
    config = RetentionPolicyConfig.default()

    assert set(config.policies) == {
        DataCategory.TRANSCRIPT,
        DataCategory.SESSION,
        DataCategory.SUBAGENT,
        DataCategory.GOAL,
        DataCategory.RAG,
        DataCategory.WORKTREE,
    }
    assert config.policies[DataCategory.TRANSCRIPT].owner == "local-user"
    assert config.policies[DataCategory.TRANSCRIPT].retention_days == 90
    assert config.policies[DataCategory.TRANSCRIPT].delete_semantics is DeleteSemantics.HARD_DELETE
    assert config.policies[DataCategory.RAG].delete_semantics is DeleteSemantics.SOFT_DELETE
    assert config.policies[DataCategory.WORKTREE].retention_days == 7


def test_retention_policy_loads_toml_overrides(tmp_path: Path):
    config_path = tmp_path / "retention.toml"
    config_path.write_text(
        """
[transcript]
owner = "workspace-admin"
retention_days = 14
delete_semantics = "archive"
backup = true

[worktree]
retention_days = 2
""".strip(),
        encoding="utf-8",
    )

    config = RetentionPolicyConfig.from_toml(config_path)

    transcript = config.policies[DataCategory.TRANSCRIPT]
    assert transcript.owner == "workspace-admin"
    assert transcript.retention_days == 14
    assert transcript.delete_semantics is DeleteSemantics.ARCHIVE
    assert transcript.backup is True
    assert config.policies[DataCategory.WORKTREE].retention_days == 2
    assert config.policies[DataCategory.WORKTREE].delete_semantics is DeleteSemantics.HARD_DELETE
