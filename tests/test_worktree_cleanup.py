from __future__ import annotations

import os
import time
from pathlib import Path

from voice_code.data_retention import cleanup_expired_worktrees


def test_cleanup_expired_worktrees_removes_old_worktrees_and_temp_files(tmp_path: Path):
    root = tmp_path / ".reasoning" / "worktrees"
    old_worktree = root / "old-goal"
    fresh_worktree = root / "fresh-goal"
    old_worktree.mkdir(parents=True)
    fresh_worktree.mkdir()
    old_temp = root / "stale.patch"
    fresh_temp = root / "fresh.tmp"
    old_temp.write_text("patch", encoding="utf-8")
    fresh_temp.write_text("tmp", encoding="utf-8")
    old_timestamp = time.time() - 10 * 24 * 60 * 60
    os.utime(old_worktree, (old_timestamp, old_timestamp))
    os.utime(old_temp, (old_timestamp, old_timestamp))

    result = cleanup_expired_worktrees(root, retention_days=7)

    assert result.deleted_paths == [old_worktree, old_temp]
    assert not old_worktree.exists()
    assert not old_temp.exists()
    assert fresh_worktree.exists()
    assert fresh_temp.exists()
