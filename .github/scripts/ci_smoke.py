from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from voice_code.memory.repository import MemoryRepository

ROOT = Path(__file__).resolve().parents[2]


def migrate_v3_to_v4() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "memory.db"
        with closing(sqlite3.connect(database_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE memory_entries(
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_key TEXT,
                    project_key_key TEXT NOT NULL DEFAULT '',
                    scope TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE memory_fts(memory_id, content, summary);
                CREATE TABLE memory_outbox(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    memory_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                PRAGMA user_version=3;
                """
            )
            connection.commit()
        repository = MemoryRepository(database_path)
        with repository._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != 4:
            raise RuntimeError(f"expected migrated memory schema v4, got v{version}")


def run_cli_help() -> None:
    subprocess.run(
        [sys.executable, "-m", "voice_code.cli", "--plain", "--help"],
        cwd=ROOT,
        check=True,
    )


def run_milvus_if_available() -> None:
    if not os.getenv("MILVUS_URI"):
        return
    subprocess.run(
        [sys.executable, "-m", "voice_code.memory.cli", "health"],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--migration-only", action="store_true")
    args = parser.parse_args()
    migrate_v3_to_v4()
    if args.migration_only:
        return
    run_milvus_if_available()
    run_cli_help()


if __name__ == "__main__":
    main()
