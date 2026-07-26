from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voice_code.memory.candidates import (
    CandidateStatus,
    ExtractionJob,
    ExtractionMode,
    MemoryAuthority,
    MemoryCandidate,
    MemoryMutationDecision,
    MutationAction,
    SensitivityDecision,
    StoredMemoryCandidate,
)
from voice_code.memory.rag_models import (
    IndexOperation,
    MemoryHit,
    MemoryKind,
    MemoryRecord,
    MemoryReviewState,
    MemoryScope,
    MemoryStatus,
    OutboxEvent,
    RememberResult,
    VectorHit,
)
from voice_code.security.redaction import redact_secrets

_SCHEMA_VERSION = 4
_MAX_CONTENT_CHARS = 8_000


@dataclass(frozen=True, slots=True)
class BackupResult:
    backup_path: Path
    integrity_check: str
    restore_steps: str


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        suppress = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return suppress


class MemoryRepository:
    """Transactional source of truth for persistent memories and index work."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=5,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > _SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported memory database version: {version}")
            if version == 0:
                connection.executescript(_MIGRATION_V1)
                version = 1
                connection.execute("PRAGMA user_version=1")
            if version == 1:
                connection.executescript(_MIGRATION_V2)
                connection.execute("PRAGMA user_version=2")
                version = 2
            if version == 2:
                connection.executescript(_MIGRATION_V3)
                connection.execute("PRAGMA user_version=3")
                version = 3
            if version == 3:
                connection.executescript(_MIGRATION_V4)
                connection.execute("PRAGMA user_version=4")

    def verify_integrity(self) -> str:
        with self._connect() as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row is not None else "missing"

    def backup_online(self, backup_path: Path) -> BackupResult:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source:
            with sqlite3.connect(backup_path) as target:
                source.backup(target)
        restored = MemoryRepository(backup_path)
        integrity_check = restored.verify_integrity()
        return BackupResult(
            backup_path=backup_path,
            integrity_check=integrity_check,
            restore_steps=(
                "Stop writers, keep the damaged database unchanged, then run "
                f"uv run reasoning-memory restore --backup {backup_path} --database "
                "<restored-memory.db>; run uv run reasoning-memory health before reuse."
            ),
        )

    @classmethod
    def restore_from_backup(cls, backup_path: Path, database_path: Path) -> MemoryRepository:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        if database_path.exists():
            raise FileExistsError(f"Restore target already exists: {database_path}")
        probe = cls(backup_path)
        integrity_check = probe.verify_integrity()
        if integrity_check != "ok":
            raise RuntimeError(f"Backup integrity check failed: {integrity_check}")
        shutil.copy2(backup_path, database_path)
        restored = cls(database_path)
        restored_integrity = restored.verify_integrity()
        if restored_integrity != "ok":
            raise RuntimeError(f"Restored database integrity check failed: {restored_integrity}")
        return restored

    def remember(
        self,
        *,
        user_id: str,
        project_key: str | None,
        scope: MemoryScope,
        kind: MemoryKind,
        content: str,
        source_session_id: str,
    ) -> RememberResult:
        normalized = _normalize_content(content)
        _validate_identity(user_id, project_key, scope)
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM memory_entries
                WHERE user_id = ? AND scope = ? AND project_key_key = ?
                  AND content_hash = ? AND status = 'active'
                """,
                (user_id, scope.value, project_key or "", content_hash),
            ).fetchone()
            if existing is not None:
                return RememberResult(entry=_row_to_record(existing), created=False)

            memory_id = f"mem_{uuid.uuid4().hex}"
            summary = normalized[:240]
            connection.execute(
                """
                INSERT INTO memory_entries(
                    id, user_id, project_key, project_key_key, scope, kind, content,
                    summary, status, source_session_id, content_hash, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 1, ?, ?)
                """,
                (
                    memory_id,
                    user_id,
                    project_key,
                    project_key or "",
                    scope.value,
                    kind.value,
                    normalized,
                    summary,
                    source_session_id,
                    content_hash,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO memory_fts(memory_id, content, summary) VALUES (?, ?, ?)",
                (memory_id, normalized, summary),
            )
            self._append_outbox(connection, memory_id, IndexOperation.UPSERT, 1, now)
            row = connection.execute(
                "SELECT * FROM memory_entries WHERE id = ?", (memory_id,)
            ).fetchone()
        return RememberResult(entry=_row_to_record(row), created=True)

    def import_legacy(
        self,
        *,
        memory_id: str,
        user_id: str,
        project_key: str | None,
        scope: MemoryScope,
        kind: MemoryKind,
        content: str,
        source_session_id: str,
        legacy_source_path: str,
        created_at: datetime,
        updated_at: datetime,
    ) -> RememberResult:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", memory_id):
            raise ValueError("Invalid legacy memory ID")
        normalized = _normalize_content(content)
        _validate_identity(user_id, project_key, scope)
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM memory_entries
                WHERE id = ? OR (
                    user_id = ? AND scope = ? AND project_key_key = ?
                    AND content_hash = ? AND status = 'active'
                )
                LIMIT 1
                """,
                (memory_id, user_id, scope.value, project_key or "", content_hash),
            ).fetchone()
            if existing is not None:
                self._append_migration_journal(
                    connection,
                    legacy_id=memory_id,
                    memory_id=str(existing["id"]),
                    source_path=legacy_source_path,
                    content_hash=content_hash,
                )
                return RememberResult(entry=_row_to_record(existing), created=False)
            summary = normalized[:240]
            connection.execute(
                """
                INSERT INTO memory_entries(
                    id, user_id, project_key, project_key_key, scope, kind, content,
                    summary, status, source_session_id, content_hash, version,
                    created_at, updated_at, authority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 1, ?, ?, 'migrated')
                """,
                (
                    memory_id,
                    user_id,
                    project_key,
                    project_key or "",
                    scope.value,
                    kind.value,
                    normalized,
                    summary,
                    source_session_id,
                    content_hash,
                    created_at.isoformat(),
                    updated_at.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO memory_fts(memory_id, content, summary) VALUES (?, ?, ?)",
                (memory_id, normalized, summary),
            )
            self._append_outbox(
                connection,
                memory_id,
                IndexOperation.UPSERT,
                1,
                datetime.now(UTC),
            )
            self._append_migration_journal(
                connection,
                legacy_id=memory_id,
                memory_id=memory_id,
                source_path=legacy_source_path,
                content_hash=content_hash,
            )
            row = connection.execute(
                "SELECT * FROM memory_entries WHERE id = ?", (memory_id,)
            ).fetchone()
        return RememberResult(entry=_row_to_record(row), created=True)

    def get(self, memory_id: str, *, user_id: str) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_entries WHERE id = ? AND user_id = ? AND status = 'active'",
                (memory_id, user_id),
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def get_for_index(self, memory_id: str) -> MemoryRecord | None:
        """Read an active record for the internal outbox worker."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_entries WHERE id = ? AND status = 'active'",
                (memory_id,),
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def list(self, *, user_id: str, limit: int = 100) -> list[MemoryRecord]:
        bounded_limit = min(max(limit, 1), 500)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_entries
                WHERE user_id = ? AND status = 'active'
                ORDER BY updated_at DESC LIMIT ?
                """,
                (user_id, bounded_limit),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def save_candidate(
        self,
        candidate: MemoryCandidate,
        *,
        status: CandidateStatus,
    ) -> bool:
        with self._connect() as connection:
            cursor = self._insert_candidate(connection, candidate, status)
        return cursor.rowcount == 1

    def apply_candidate_decision(
        self,
        candidate: MemoryCandidate,
        decision: MemoryMutationDecision,
        *,
        authority: MemoryAuthority,
        actor_type: str,
        actor_id: str,
    ) -> tuple[MemoryRecord | None, bool]:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_candidate(connection, candidate, CandidateStatus.PENDING)
            previous = connection.execute(
                """
                SELECT memory_id FROM memory_mutation_log
                WHERE candidate_id = ? ORDER BY created_at DESC LIMIT 1
                """,
                (candidate.candidate_id,),
            ).fetchone()
            if previous is not None:
                row = None
                if previous["memory_id"] is not None:
                    row = connection.execute(
                        "SELECT * FROM memory_entries WHERE id = ?",
                        (previous["memory_id"],),
                    ).fetchone()
                return (_row_to_record(row) if row is not None else None, False)

            memory_id, before_version, after_version = self._apply_memory_mutation(
                connection,
                candidate,
                decision,
                authority=authority,
                now=now,
            )
            candidate_status = _candidate_status_for_action(decision.action)
            connection.execute(
                """
                UPDATE memory_candidates
                SET status = ?, reason_code = ?, decided_at = ?
                WHERE candidate_id = ?
                """,
                (
                    candidate_status.value,
                    decision.reason_code,
                    now.isoformat(),
                    candidate.candidate_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_mutation_log(
                    mutation_id, candidate_id, memory_id, target_memory_id, action,
                    reason_code, actor_type, actor_id, before_version, after_version,
                    details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)
                """,
                (
                    f"mut_{uuid.uuid4().hex}",
                    candidate.candidate_id,
                    memory_id,
                    decision.target_memory_id,
                    decision.action.value,
                    decision.reason_code,
                    actor_type[:32],
                    actor_id[:128],
                    before_version,
                    after_version,
                    now.isoformat(),
                ),
            )
            row = (
                connection.execute(
                    "SELECT * FROM memory_entries WHERE id = ?", (memory_id,)
                ).fetchone()
                if memory_id
                else None
            )
        return (_row_to_record(row) if row is not None else None, True)

    def list_candidates(
        self,
        *,
        user_id: str,
        status: CandidateStatus | None = None,
        limit: int = 100,
    ) -> list[StoredMemoryCandidate]:
        query = "SELECT * FROM memory_candidates WHERE user_id = ?"
        params: list[object] = [user_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(min(max(limit, 1), 500))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_candidate(row) for row in rows]

    def approve_candidate(self, candidate_id: str, *, user_id: str) -> MemoryRecord:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id = ? AND user_id = ?",
                (candidate_id, user_id),
            ).fetchone()
            if row is None:
                raise ValueError("Memory candidate not found")
            existing_mutation = connection.execute(
                """
                SELECT memory_id FROM memory_mutation_log
                WHERE candidate_id = ? AND memory_id IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
            if str(row["status"]) == CandidateStatus.ACCEPTED.value and existing_mutation:
                memory_row = connection.execute(
                    "SELECT * FROM memory_entries WHERE id = ? AND user_id = ?",
                    (existing_mutation["memory_id"], user_id),
                ).fetchone()
                if memory_row is not None:
                    return _row_to_record(memory_row)
            if str(row["status"]) not in {
                CandidateStatus.PENDING.value,
                CandidateStatus.CONFIRMATION_PENDING.value,
            }:
                raise ValueError("Memory candidate is not awaiting approval")
            stored = _row_to_candidate(row)
            target_row = connection.execute(
                """
                SELECT target_memory_id FROM memory_mutation_log
                WHERE candidate_id = ? AND target_memory_id IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
            target_id = str(target_row["target_memory_id"]) if target_row else None
            if target_id:
                target = connection.execute(
                    "SELECT version FROM memory_entries WHERE id = ? AND user_id = ?",
                    (target_id, user_id),
                ).fetchone()
                if target is not None:
                    version = int(target["version"]) + 1
                    connection.execute(
                        """
                        UPDATE memory_entries
                        SET status = 'superseded', review_state = 'accepted',
                            version = ?, updated_at = ?
                        WHERE id = ? AND user_id = ?
                        """,
                        (version, now.isoformat(), target_id, user_id),
                    )
                    connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (target_id,))
                    self._append_outbox(
                        connection, target_id, IndexOperation.DELETE, version, now
                    )
            memory_id = self._insert_candidate_memory(
                connection,
                stored.candidate,
                authority=MemoryAuthority.APPROVED_EXTRACTION,
                now=now,
                supersedes_memory_id=target_id,
            )
            connection.execute(
                """
                UPDATE memory_candidates
                SET status = 'accepted', reason_code = 'USER_APPROVED', decided_at = ?
                WHERE candidate_id = ? AND user_id = ?
                """,
                (now.isoformat(), candidate_id, user_id),
            )
            self._insert_mutation_log(
                connection,
                candidate_id=candidate_id,
                memory_id=memory_id,
                target_memory_id=target_id,
                action=MutationAction.CREATE,
                reason_code="USER_APPROVED",
                actor_type="user",
                actor_id=user_id,
                before_version=None,
                after_version=1,
                now=now,
            )
            memory_row = connection.execute(
                "SELECT * FROM memory_entries WHERE id = ?", (memory_id,)
            ).fetchone()
        return _row_to_record(memory_row)

    def reject_candidate(self, candidate_id: str, *, user_id: str) -> bool:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM memory_candidates WHERE candidate_id = ? AND user_id = ?",
                (candidate_id, user_id),
            ).fetchone()
            if row is None:
                return False
            if str(row["status"]) == CandidateStatus.REJECTED.value:
                return True
            if str(row["status"]) == CandidateStatus.ACCEPTED.value:
                raise ValueError("Accepted memory candidate cannot be rejected")
            connection.execute(
                """
                UPDATE memory_candidates
                SET status = 'rejected', reason_code = 'USER_REJECTED', decided_at = ?
                WHERE candidate_id = ? AND user_id = ?
                """,
                (now.isoformat(), candidate_id, user_id),
            )
            self._insert_mutation_log(
                connection,
                candidate_id=candidate_id,
                memory_id=None,
                target_memory_id=None,
                action=MutationAction.REJECT,
                reason_code="USER_REJECTED",
                actor_type="user",
                actor_id=user_id,
                before_version=None,
                after_version=None,
                now=now,
            )
        return True

    def list_conflicts(self, *, user_id: str) -> list[StoredMemoryCandidate]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_candidates
                WHERE user_id = ? AND status = 'confirmation_pending'
                  AND reason_code LIKE '%CONFLICT%'
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [_row_to_candidate(row) for row in rows]

    def resolve_conflict_keep_existing(
        self,
        candidate_id: str,
        *,
        keep_memory_id: str,
        user_id: str,
    ) -> MemoryRecord:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = connection.execute(
                """
                SELECT * FROM memory_candidates
                WHERE candidate_id = ? AND user_id = ?
                  AND status = 'confirmation_pending'
                """,
                (candidate_id, user_id),
            ).fetchone()
            target = connection.execute(
                """
                SELECT target_memory_id FROM memory_mutation_log
                WHERE candidate_id = ? AND action = 'conflict'
                ORDER BY created_at DESC LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
            if candidate is None or target is None:
                raise ValueError("Memory conflict not found")
            if str(target["target_memory_id"]) != keep_memory_id:
                raise ValueError("Conflict keep target does not match existing memory")
            memory = connection.execute(
                "SELECT * FROM memory_entries WHERE id = ? AND user_id = ?",
                (keep_memory_id, user_id),
            ).fetchone()
            if memory is None or str(memory["status"]) != MemoryStatus.CONFLICTED.value:
                raise ValueError("Conflicted memory not found")
            version = int(memory["version"]) + 1
            connection.execute(
                """
                UPDATE memory_entries
                SET status = 'active', review_state = 'accepted', version = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (version, now.isoformat(), keep_memory_id, user_id),
            )
            connection.execute(
                "INSERT INTO memory_fts(memory_id, content, summary) VALUES (?, ?, ?)",
                (keep_memory_id, str(memory["content"]), str(memory["summary"])),
            )
            connection.execute(
                """
                UPDATE memory_candidates
                SET status = 'rejected', reason_code = 'USER_KEPT_EXISTING', decided_at = ?
                WHERE candidate_id = ? AND user_id = ?
                """,
                (now.isoformat(), candidate_id, user_id),
            )
            self._append_outbox(
                connection, keep_memory_id, IndexOperation.UPSERT, version, now
            )
            self._insert_mutation_log(
                connection,
                candidate_id=candidate_id,
                memory_id=keep_memory_id,
                target_memory_id=keep_memory_id,
                action=MutationAction.REJECT,
                reason_code="USER_KEPT_EXISTING",
                actor_type="user",
                actor_id=user_id,
                before_version=version - 1,
                after_version=version,
                now=now,
            )
            updated = connection.execute(
                "SELECT * FROM memory_entries WHERE id = ?", (keep_memory_id,)
            ).fetchone()
        return _row_to_record(updated)

    def explain_memory(self, memory_id: str, *, user_id: str) -> dict[str, object]:
        with self._connect() as connection:
            memory = connection.execute(
                "SELECT * FROM memory_entries WHERE id = ? AND user_id = ?",
                (memory_id, user_id),
            ).fetchone()
            if memory is None:
                raise ValueError("Memory not found")
            evidence = connection.execute(
                """
                SELECT source_session_id, source_turn_id, evidence_text, created_at
                FROM memory_evidence WHERE memory_id = ? ORDER BY created_at
                """,
                (memory_id,),
            ).fetchall()
            mutations = connection.execute(
                """
                SELECT action, reason_code, actor_type, before_version,
                       after_version, created_at
                FROM memory_mutation_log
                WHERE memory_id = ? OR target_memory_id = ? ORDER BY created_at
                """,
                (memory_id, memory_id),
            ).fetchall()
        return {
            "memory_id": memory_id,
            "authority": str(memory["authority"]),
            "source_session_id": str(memory["source_session_id"]),
            "created_at": str(memory["created_at"]),
            "updated_at": str(memory["updated_at"]),
            "evidence": [dict(row) for row in evidence],
            "mutations": [dict(row) for row in mutations],
        }

    def export_session_records(self, *, session_id: str, user_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            memories = connection.execute(
                """
                SELECT * FROM memory_entries
                WHERE source_session_id = ? AND user_id = ? AND status = 'active'
                ORDER BY created_at
                """,
                (session_id, user_id),
            ).fetchall()
            memory_ids = [str(row["id"]) for row in memories]
            evidence: list[sqlite3.Row] = []
            if memory_ids:
                placeholders = ",".join("?" for _ in memory_ids)
                evidence = connection.execute(
                    f"""
                    SELECT * FROM memory_evidence
                    WHERE memory_id IN ({placeholders})
                    ORDER BY created_at
                    """,
                    tuple(memory_ids),
                ).fetchall()
        evidence_by_memory: dict[str, list[dict[str, object]]] = {}
        for row in evidence:
            evidence_by_memory.setdefault(str(row["memory_id"]), []).append(dict(row))
        return [
            {
                "memory_id": str(row["id"]),
                "user_id": str(row["user_id"]),
                "project_key": str(row["project_key"]) if row["project_key"] is not None else None,
                "scope": str(row["scope"]),
                "kind": str(row["kind"]),
                "content": str(row["content"]),
                "summary": str(row["summary"]),
                "source_session_id": str(row["source_session_id"]),
                "version": int(row["version"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "evidence": evidence_by_memory.get(str(row["id"]), []),
            }
            for row in memories
        ]

    def delete_session_records(self, *, session_id: str, user_id: str) -> dict[str, int]:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT id, version FROM memory_entries
                WHERE source_session_id = ? AND user_id = ? AND status = 'active'
                """,
                (session_id, user_id),
            ).fetchall()
            memory_ids = [str(row["id"]) for row in rows]
            evidence_deleted = 0
            if memory_ids:
                placeholders = ",".join("?" for _ in memory_ids)
                evidence_deleted = connection.execute(
                    f"DELETE FROM memory_evidence WHERE memory_id IN ({placeholders})",
                    tuple(memory_ids),
                ).rowcount
                connection.execute(
                    f"DELETE FROM memory_fts WHERE memory_id IN ({placeholders})",
                    tuple(memory_ids),
                )
            for row in rows:
                version = int(row["version"]) + 1
                memory_id = str(row["id"])
                connection.execute(
                    """
                    UPDATE memory_entries
                    SET status = 'deleted', version = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (version, now.isoformat(), memory_id, user_id),
                )
                self._append_outbox(connection, memory_id, IndexOperation.DELETE, version, now)
            extraction_deleted = connection.execute(
                "DELETE FROM memory_extraction_jobs WHERE source_session_id = ? AND user_id = ?",
                (session_id, user_id),
            ).rowcount
            candidate_deleted = connection.execute(
                "DELETE FROM memory_candidates WHERE source_session_id = ? AND user_id = ?",
                (session_id, user_id),
            ).rowcount
        return {
            "memory_deleted": len(rows),
            "evidence_deleted": int(evidence_deleted),
            "extraction_deleted": int(extraction_deleted),
            "candidate_deleted": int(candidate_deleted),
        }

    def edit_memory(self, memory_id: str, content: str, *, user_id: str) -> MemoryRecord:
        normalized = _normalize_content(content)
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT version FROM memory_entries
                WHERE id = ? AND user_id = ? AND status = 'active'
                """,
                (memory_id, user_id),
            ).fetchone()
            if row is None:
                raise ValueError("Memory not found")
            before_version = int(row["version"])
            after_version = before_version + 1
            connection.execute(
                """
                UPDATE memory_entries
                SET content = ?, summary = ?, content_hash = ?, authority = 'explicit',
                    confidence = NULL, version = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    normalized,
                    normalized[:240],
                    content_hash,
                    after_version,
                    now.isoformat(),
                    memory_id,
                    user_id,
                ),
            )
            connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
            connection.execute(
                "INSERT INTO memory_fts(memory_id, content, summary) VALUES (?, ?, ?)",
                (memory_id, normalized, normalized[:240]),
            )
            self._append_outbox(
                connection, memory_id, IndexOperation.UPSERT, after_version, now
            )
            self._insert_mutation_log(
                connection,
                candidate_id=None,
                memory_id=memory_id,
                target_memory_id=None,
                action=MutationAction.MERGE,
                reason_code="USER_EDITED",
                actor_type="user",
                actor_id=user_id,
                before_version=before_version,
                after_version=after_version,
                now=now,
            )
            updated = connection.execute(
                "SELECT * FROM memory_entries WHERE id = ?", (memory_id,)
            ).fetchone()
        return _row_to_record(updated)

    def consolidate_automatic_pair(
        self,
        keeper_id: str,
        duplicate_id: str,
        *,
        user_id: str,
        reason_code: str,
    ) -> bool:
        if keeper_id == duplicate_id:
            return False
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM memory_entries
                WHERE id IN (?, ?) AND user_id = ? AND status = 'active'
                """,
                (keeper_id, duplicate_id, user_id),
            ).fetchall()
            entries = {str(row["id"]): row for row in rows}
            keeper = entries.get(keeper_id)
            duplicate = entries.get(duplicate_id)
            if keeper is None or duplicate is None:
                return False
            comparable = (
                keeper["authority"] == MemoryAuthority.AUTOMATIC_EXTRACTION.value
                and duplicate["authority"] == MemoryAuthority.AUTOMATIC_EXTRACTION.value
                and keeper["project_key_key"] == duplicate["project_key_key"]
                and keeper["scope"] == duplicate["scope"]
                and keeper["kind"] == duplicate["kind"]
            )
            if not comparable:
                return False
            keeper_version = int(keeper["version"]) + 1
            duplicate_version = int(duplicate["version"]) + 1
            connection.execute(
                """
                UPDATE memory_entries SET version = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (keeper_version, now.isoformat(), keeper_id, user_id),
            )
            connection.execute(
                """
                UPDATE memory_entries
                SET status = 'superseded', version = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    duplicate_version,
                    now.isoformat(),
                    duplicate_id,
                    user_id,
                ),
            )
            connection.execute(
                "UPDATE memory_evidence SET memory_id = ? WHERE memory_id = ?",
                (keeper_id, duplicate_id),
            )
            connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (duplicate_id,))
            self._append_outbox(
                connection, keeper_id, IndexOperation.UPSERT, keeper_version, now
            )
            self._append_outbox(
                connection, duplicate_id, IndexOperation.DELETE, duplicate_version, now
            )
            self._insert_mutation_log(
                connection,
                candidate_id=None,
                memory_id=keeper_id,
                target_memory_id=duplicate_id,
                action=MutationAction.MERGE,
                reason_code=reason_code,
                actor_type="consolidator",
                actor_id="memory-consolidator",
                before_version=keeper_version - 1,
                after_version=keeper_version,
                now=now,
            )
        return True

    def enqueue_extraction_job(
        self,
        *,
        user_id: str,
        project_key: str | None,
        source_session_id: str,
        source_turn_id: str,
        user_input: str,
        assistant_response: str,
        mode: ExtractionMode,
    ) -> bool:
        if mode is ExtractionMode.OFF:
            return False
        if redact_secrets(user_input) != user_input or (
            redact_secrets(assistant_response) != assistant_response
        ):
            raise ValueError("Extraction turn contains sensitive content")
        if not user_input.strip() or len(user_input) > 8_000:
            raise ValueError("Invalid extraction user input")
        if len(assistant_response) > 8_000:
            raise ValueError("Invalid extraction assistant response")
        now = datetime.now(UTC)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO memory_extraction_jobs(
                    job_id, user_id, project_key, source_session_id, source_turn_id,
                    user_input, assistant_response, mode, attempts, available_at,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'pending', ?)
                """,
                (
                    f"job_{uuid.uuid4().hex}",
                    user_id,
                    project_key,
                    source_session_id,
                    source_turn_id,
                    user_input,
                    assistant_response,
                    mode.value,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return cursor.rowcount == 1

    def claim_extraction_jobs(
        self,
        *,
        limit: int,
        lease_seconds: int,
    ) -> list[ExtractionJob]:
        now = datetime.now(UTC)
        leased_until = now + timedelta(seconds=max(lease_seconds, 1))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM memory_extraction_jobs
                WHERE status = 'pending' AND available_at <= ?
                  AND (leased_until IS NULL OR leased_until <= ?)
                ORDER BY created_at LIMIT ?
                """,
                (now.isoformat(), now.isoformat(), min(max(limit, 1), 100)),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE memory_extraction_jobs
                    SET leased_until = ?, attempts = attempts + 1
                    WHERE job_id = ? AND status = 'pending'
                    """,
                    (leased_until.isoformat(), row["job_id"]),
                )
        return [
            ExtractionJob(
                job_id=str(row["job_id"]),
                user_id=str(row["user_id"]),
                project_key=(
                    str(row["project_key"]) if row["project_key"] is not None else None
                ),
                source_session_id=str(row["source_session_id"]),
                source_turn_id=str(row["source_turn_id"]),
                user_input=str(row["user_input"]),
                assistant_response=str(row["assistant_response"]),
                mode=ExtractionMode(str(row["mode"])),
                attempts=int(row["attempts"]) + 1,
            )
            for row in rows
        ]

    def complete_extraction_job(self, job_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE memory_extraction_jobs
                SET status = 'completed', completed_at = ?, leased_until = NULL,
                    last_error_code = NULL
                WHERE job_id = ?
                """,
                (now, job_id),
            )

    def fail_extraction_job(
        self,
        job_id: str,
        *,
        error_code: str,
        delay_seconds: int,
        terminal: bool = False,
    ) -> None:
        available_at = datetime.now(UTC) + timedelta(seconds=max(delay_seconds, 1))
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE memory_extraction_jobs
                SET status = ?, available_at = ?, leased_until = NULL,
                    last_error_code = ?
                WHERE job_id = ?
                """,
                (
                    "failed" if terminal else "pending",
                    available_at.isoformat(),
                    error_code[:64],
                    job_id,
                ),
            )

    def record_extraction_run(
        self,
        *,
        run_id: str,
        job: ExtractionJob,
        extractor_model: str,
        prompt_version: str,
        candidate_count: int,
        accepted_count: int,
        status: str,
        latency_ms: float,
        error_code: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO memory_extraction_runs(
                    run_id, user_id, project_key, source_session_id, source_turn_id,
                    mode, extractor_model, prompt_version, candidate_count,
                    accepted_count, status, latency_ms, error_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    job.user_id,
                    job.project_key,
                    job.source_session_id,
                    job.source_turn_id,
                    job.mode.value,
                    extractor_model,
                    prompt_version,
                    candidate_count,
                    accepted_count,
                    status[:32],
                    max(latency_ms, 0),
                    error_code[:64] if error_code else None,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def find_exact_active(
        self,
        *,
        user_id: str,
        project_key: str | None,
        scope: MemoryScope,
        content: str,
    ) -> MemoryRecord | None:
        content_hash = hashlib.sha256(" ".join(content.split()).encode("utf-8")).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM memory_entries
                WHERE user_id = ? AND scope = ? AND project_key_key = ?
                  AND content_hash = ? AND status = 'active'
                """,
                (
                    user_id,
                    scope.value,
                    project_key or "" if scope is MemoryScope.PROJECT else "",
                    content_hash,
                ),
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def hydrate_active(
        self,
        hits: list[VectorHit],
        *,
        user_id: str,
        project_key: str | None,
    ) -> list[MemoryHit]:
        if not hits:
            return []
        ids = [hit.memory_id for hit in hits[:100]]
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM memory_entries
                WHERE id IN ({placeholders}) AND user_id = ? AND status = 'active'
                  AND (scope = 'user' OR (scope = 'project' AND project_key_key = ?))
                """,
                (*ids, user_id, project_key or ""),
            ).fetchall()
        entries = {str(row["id"]): _row_to_record(row) for row in rows}
        hydrated: list[MemoryHit] = []
        for hit in hits:
            entry = entries.get(hit.memory_id)
            if entry is not None and entry.version == hit.memory_version:
                hydrated.append(MemoryHit(entry=entry, score=hit.score, backend="milvus"))
        return hydrated

    def archive(self, memory_id: str, *, user_id: str) -> bool:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT version FROM memory_entries
                WHERE id = ? AND user_id = ? AND status = 'active'
                """,
                (memory_id, user_id),
            ).fetchone()
            if row is None:
                return False
            version = int(row["version"]) + 1
            connection.execute(
                """
                UPDATE memory_entries
                SET status = 'archived', version = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (version, now.isoformat(), memory_id, user_id),
            )
            connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
            self._append_outbox(connection, memory_id, IndexOperation.DELETE, version, now)
        return True

    def search_lexical(
        self,
        query: str,
        *,
        user_id: str,
        project_key: str | None = None,
        limit: int = 5,
    ) -> list[MemoryHit]:
        terms = _query_terms(query)
        if not terms:
            return []
        bounded_limit = min(max(limit, 1), 20)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_entries
                WHERE user_id = ? AND status = 'active'
                  AND (scope = 'user' OR (scope = 'project' AND project_key_key = ?))
                ORDER BY updated_at DESC LIMIT 500
                """,
                (user_id, project_key or ""),
            ).fetchall()
        scored: list[MemoryHit] = []
        for row in rows:
            entry = _row_to_record(row)
            lowered = entry.content.casefold()
            matches = sum(1 for term in terms if term.casefold() in lowered)
            if matches:
                scored.append(
                    MemoryHit(entry=entry, score=matches / len(terms), backend="sqlite_fts")
                )
        scored.sort(key=lambda hit: (hit.score, hit.entry.updated_at), reverse=True)
        return scored[:bounded_limit]

    def claim_outbox(self, *, limit: int, lease_seconds: int) -> list[OutboxEvent]:
        now = datetime.now(UTC)
        leased_until = now + timedelta(seconds=max(lease_seconds, 1))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM memory_index_outbox
                WHERE delivered_at IS NULL AND available_at <= ?
                  AND (leased_until IS NULL OR leased_until <= ?)
                ORDER BY created_at LIMIT ?
                """,
                (now.isoformat(), now.isoformat(), min(max(limit, 1), 100)),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE memory_index_outbox
                    SET leased_until = ?, attempts = attempts + 1
                    WHERE event_id = ?
                    """,
                    (leased_until.isoformat(), row["event_id"]),
                )
        return [
            OutboxEvent(
                event_id=str(row["event_id"]),
                memory_id=str(row["memory_id"]),
                operation=IndexOperation(str(row["operation"])),
                memory_version=int(row["memory_version"]),
                attempts=int(row["attempts"]) + 1,
            )
            for row in rows
        ]

    def mark_outbox_delivered(self, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE memory_index_outbox
                SET delivered_at = ?, leased_until = NULL, last_error_code = NULL
                WHERE event_id = ?
                """,
                (datetime.now(UTC).isoformat(), event_id),
            )

    def mark_outbox_failed(
        self,
        event_id: str,
        *,
        error_code: str,
        delay_seconds: int,
        terminal: bool = False,
    ) -> None:
        available_at = datetime.now(UTC) + timedelta(seconds=max(delay_seconds, 1))
        with self._connect() as connection:
            if terminal:
                row = connection.execute(
                    "SELECT * FROM memory_index_outbox WHERE event_id = ? AND delivered_at IS NULL",
                    (event_id,),
                ).fetchone()
                if row is not None:
                    self._append_dead_letter(
                        connection,
                        source_type="outbox",
                        source_id=event_id,
                        error_code=error_code,
                        attempts=int(row["attempts"]),
                        memory_id=str(row["memory_id"]),
                    )
            connection.execute(
                """
                UPDATE memory_index_outbox
                SET available_at = ?, leased_until = NULL, last_error_code = ?
                WHERE event_id = ? AND delivered_at IS NULL
                """,
                (available_at.isoformat(), error_code[:64], event_id),
            )

    def list_dead_letters(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_dead_letters
                ORDER BY created_at
                """
            ).fetchall()
        return [_dead_letter_row_to_dict(row) for row in rows]

    def explain_dead_letter(self, dead_letter_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_dead_letters WHERE dead_letter_id = ?",
                (dead_letter_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Dead-letter not found")
        return _dead_letter_row_to_dict(row)

    def replay_dead_letter(self, dead_letter_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM memory_dead_letters
                WHERE dead_letter_id = ? AND replayed_at IS NULL
                """,
                (dead_letter_id,),
            ).fetchone()
            if row is None:
                return False
            source_type = str(row["source_type"])
            source_id = str(row["source_id"])
            if source_type == "outbox":
                connection.execute(
                    """
                    UPDATE memory_index_outbox
                    SET attempts = 0, available_at = ?, leased_until = NULL, last_error_code = NULL
                    WHERE event_id = ? AND delivered_at IS NULL
                    """,
                    (now, source_id),
                )
            elif source_type == "extraction":
                connection.execute(
                    """
                    UPDATE memory_extraction_jobs
                    SET attempts = 0, available_at = ?, leased_until = NULL, status = 'pending',
                        last_error_code = NULL
                    WHERE job_id = ?
                    """,
                    (now, source_id),
                )
            else:
                return False
            connection.execute(
                "UPDATE memory_dead_letters SET replayed_at = ? WHERE dead_letter_id = ?",
                (now, dead_letter_id),
            )
        return True

    def dead_letter_count(self) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM memory_dead_letters WHERE replayed_at IS NULL"
                ).fetchone()[0]
            )

    def dead_letter_extraction_job(
        self,
        job_id: str,
        *,
        error_code: str,
        attempts: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._append_dead_letter(
                connection,
                source_type="extraction",
                source_id=job_id,
                error_code=error_code,
                attempts=attempts,
            )
            connection.execute(
                """
                UPDATE memory_extraction_jobs
                SET status = 'failed', leased_until = NULL, last_error_code = ?
                WHERE job_id = ?
                """,
                (error_code[:64], job_id),
            )

    def enqueue_reindex(self) -> int:
        """Schedule every active memory for idempotent index reconstruction."""
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, version FROM memory_entries WHERE status = 'active'"
            ).fetchall()
            for row in rows:
                self._append_outbox(
                    connection,
                    str(row["id"]),
                    IndexOperation.UPSERT,
                    int(row["version"]),
                    now,
                )
        return len(rows)

    @staticmethod
    def _insert_candidate(
        connection: sqlite3.Connection,
        candidate: MemoryCandidate,
        status: CandidateStatus,
    ) -> sqlite3.Cursor:
        normalized_hash = hashlib.sha256(candidate.content.encode("utf-8")).hexdigest()
        return connection.execute(
            """
            INSERT OR IGNORE INTO memory_candidates(
                candidate_id, user_id, project_key, project_key_key, scope, kind,
                content, confidence, evidence_text, source_session_id,
                source_turn_id, extraction_run_id, extractor_model, sensitivity,
                normalized_hash, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.candidate_id,
                candidate.user_id,
                candidate.project_key,
                candidate.project_key or "",
                candidate.scope.value,
                candidate.kind.value,
                candidate.content,
                candidate.confidence,
                candidate.evidence_text,
                candidate.source_session_id,
                candidate.source_turn_id,
                candidate.extraction_run_id,
                candidate.extractor_model,
                candidate.sensitivity.value,
                normalized_hash,
                status.value,
                candidate.created_at.isoformat(),
            ),
        )

    def _apply_memory_mutation(
        self,
        connection: sqlite3.Connection,
        candidate: MemoryCandidate,
        decision: MemoryMutationDecision,
        *,
        authority: MemoryAuthority,
        now: datetime,
    ) -> tuple[str | None, int | None, int | None]:
        if decision.action in {
            MutationAction.REJECT,
            MutationAction.REQUIRE_CONFIRMATION,
        }:
            return None, None, None
        if decision.action is MutationAction.CONFLICT:
            if decision.target_memory_id:
                row = connection.execute(
                    "SELECT version FROM memory_entries WHERE id = ? AND user_id = ?",
                    (decision.target_memory_id, candidate.user_id),
                ).fetchone()
                if row is not None:
                    version = int(row["version"]) + 1
                    connection.execute(
                        """
                        UPDATE memory_entries
                        SET status = 'conflicted', review_state = 'conflict_pending',
                            version = ?, updated_at = ?
                        WHERE id = ? AND user_id = ?
                        """,
                        (
                            version,
                            now.isoformat(),
                            decision.target_memory_id,
                            candidate.user_id,
                        ),
                    )
                    connection.execute(
                        "DELETE FROM memory_fts WHERE memory_id = ?",
                        (decision.target_memory_id,),
                    )
                    self._append_outbox(
                        connection,
                        decision.target_memory_id,
                        IndexOperation.DELETE,
                        version,
                        now,
                    )
                    return None, version - 1, version
            return None, None, None
        if decision.action is MutationAction.CREATE:
            memory_id = self._insert_candidate_memory(
                connection, candidate, authority=authority, now=now
            )
            return memory_id, None, 1

        if not decision.target_memory_id:
            raise ValueError("Mutation action requires a target memory")
        target = connection.execute(
            "SELECT * FROM memory_entries WHERE id = ? AND user_id = ?",
            (decision.target_memory_id, candidate.user_id),
        ).fetchone()
        if target is None:
            raise ValueError("Mutation target memory not found")
        before_version = int(target["version"])
        if decision.action is MutationAction.IGNORE_DUPLICATE:
            self._append_evidence(connection, candidate, decision.target_memory_id, now)
            return decision.target_memory_id, before_version, before_version
        if decision.action is MutationAction.MERGE:
            after_version = before_version + 1
            content_hash = hashlib.sha256(candidate.content.encode("utf-8")).hexdigest()
            connection.execute(
                """
                UPDATE memory_entries
                SET content = ?, summary = ?, content_hash = ?, authority = ?,
                    confidence = ?, version = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    candidate.content,
                    candidate.content[:240],
                    content_hash,
                    authority.value,
                    candidate.confidence,
                    after_version,
                    now.isoformat(),
                    decision.target_memory_id,
                    candidate.user_id,
                ),
            )
            connection.execute(
                "DELETE FROM memory_fts WHERE memory_id = ?", (decision.target_memory_id,)
            )
            connection.execute(
                "INSERT INTO memory_fts(memory_id, content, summary) VALUES (?, ?, ?)",
                (decision.target_memory_id, candidate.content, candidate.content[:240]),
            )
            self._append_evidence(connection, candidate, decision.target_memory_id, now)
            self._append_outbox(
                connection,
                decision.target_memory_id,
                IndexOperation.UPSERT,
                after_version,
                now,
            )
            return decision.target_memory_id, before_version, after_version
        if decision.action is MutationAction.SUPERSEDE:
            after_version = before_version + 1
            connection.execute(
                """
                UPDATE memory_entries
                SET status = 'superseded', version = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    after_version,
                    now.isoformat(),
                    decision.target_memory_id,
                    candidate.user_id,
                ),
            )
            connection.execute(
                "DELETE FROM memory_fts WHERE memory_id = ?", (decision.target_memory_id,)
            )
            self._append_outbox(
                connection,
                decision.target_memory_id,
                IndexOperation.DELETE,
                after_version,
                now,
            )
            memory_id = self._insert_candidate_memory(
                connection,
                candidate,
                authority=authority,
                now=now,
                supersedes_memory_id=decision.target_memory_id,
            )
            return memory_id, before_version, 1
        raise ValueError(f"Unsupported mutation action: {decision.action}")

    def _insert_candidate_memory(
        self,
        connection: sqlite3.Connection,
        candidate: MemoryCandidate,
        *,
        authority: MemoryAuthority,
        now: datetime,
        supersedes_memory_id: str | None = None,
    ) -> str:
        memory_id = f"mem_{uuid.uuid4().hex}"
        content_hash = hashlib.sha256(candidate.content.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO memory_entries(
                id, user_id, project_key, project_key_key, scope, kind, content,
                summary, status, source_session_id, content_hash, version,
                created_at, updated_at, authority, confidence, supersedes_memory_id,
                review_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 1, ?, ?, ?, ?, ?, 'accepted')
            """,
            (
                memory_id,
                candidate.user_id,
                candidate.project_key,
                candidate.project_key or "",
                candidate.scope.value,
                candidate.kind.value,
                candidate.content,
                candidate.content[:240],
                candidate.source_session_id,
                content_hash,
                now.isoformat(),
                now.isoformat(),
                authority.value,
                candidate.confidence,
                supersedes_memory_id,
            ),
        )
        connection.execute(
            "INSERT INTO memory_fts(memory_id, content, summary) VALUES (?, ?, ?)",
            (memory_id, candidate.content, candidate.content[:240]),
        )
        self._append_evidence(connection, candidate, memory_id, now)
        self._append_outbox(connection, memory_id, IndexOperation.UPSERT, 1, now)
        return memory_id

    @staticmethod
    def _append_evidence(
        connection: sqlite3.Connection,
        candidate: MemoryCandidate,
        memory_id: str,
        now: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_evidence(
                evidence_id, memory_id, candidate_id, source_session_id,
                source_turn_id, evidence_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"evidence_{uuid.uuid4().hex}",
                memory_id,
                candidate.candidate_id,
                candidate.source_session_id,
                candidate.source_turn_id,
                candidate.evidence_text,
                now.isoformat(),
            ),
        )

    @staticmethod
    def _insert_mutation_log(
        connection: sqlite3.Connection,
        *,
        candidate_id: str | None,
        memory_id: str | None,
        target_memory_id: str | None,
        action: MutationAction,
        reason_code: str,
        actor_type: str,
        actor_id: str,
        before_version: int | None,
        after_version: int | None,
        now: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_mutation_log(
                mutation_id, candidate_id, memory_id, target_memory_id, action,
                reason_code, actor_type, actor_id, before_version, after_version,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)
            """,
            (
                f"mut_{uuid.uuid4().hex}",
                candidate_id,
                memory_id,
                target_memory_id,
                action.value,
                reason_code,
                actor_type[:32],
                actor_id[:128],
                before_version,
                after_version,
                now.isoformat(),
            ),
        )

    @staticmethod
    def _append_migration_journal(
        connection: sqlite3.Connection,
        *,
        legacy_id: str,
        memory_id: str,
        source_path: str,
        content_hash: str,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO memory_migration_journal(
                source_version, legacy_id, memory_id, source_path,
                content_hash, migrated_at
            ) VALUES (1, ?, ?, ?, ?, ?)
            """,
            (
                legacy_id,
                memory_id,
                source_path,
                content_hash,
                datetime.now(UTC).isoformat(),
            ),
        )

    @staticmethod
    def _append_dead_letter(
        connection: sqlite3.Connection,
        *,
        source_type: str,
        source_id: str,
        error_code: str,
        attempts: int,
        memory_id: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO memory_dead_letters(
                dead_letter_id, source_type, source_id, memory_id, error_code,
                attempts, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"dlq_{uuid.uuid4().hex}",
                source_type,
                source_id,
                memory_id,
                error_code[:64],
                max(attempts, 0),
                datetime.now(UTC).isoformat(),
            ),
        )

    @staticmethod
    def _append_outbox(
        connection: sqlite3.Connection,
        memory_id: str,
        operation: IndexOperation,
        memory_version: int,
        now: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_index_outbox(
                event_id, memory_id, operation, memory_version, attempts,
                available_at, created_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (
                f"evt_{uuid.uuid4().hex}",
                memory_id,
                operation.value,
                memory_version,
                now.isoformat(),
                now.isoformat(),
            ),
        )


def _dead_letter_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "dead_letter_id": str(row["dead_letter_id"]),
        "source_type": str(row["source_type"]),
        "source_id": str(row["source_id"]),
        "memory_id": str(row["memory_id"]) if row["memory_id"] is not None else None,
        "error_code": str(row["error_code"]),
        "attempts": int(row["attempts"]),
        "created_at": str(row["created_at"]),
        "replayed_at": str(row["replayed_at"]) if row["replayed_at"] is not None else None,
    }


def _normalize_content(content: str) -> str:
    normalized = " ".join(content.split()).strip()
    if not normalized:
        raise ValueError("Memory content must not be empty")
    if len(normalized) > _MAX_CONTENT_CHARS:
        raise ValueError(f"Memory content exceeds {_MAX_CONTENT_CHARS} characters")
    return normalized


def _validate_identity(user_id: str, project_key: str | None, scope: MemoryScope) -> None:
    if not user_id.strip() or len(user_id) > 128:
        raise ValueError("Invalid memory user_id")
    if scope is MemoryScope.PROJECT and not project_key:
        raise ValueError("Project memory requires project_key")
    if project_key is not None and len(project_key) > 128:
        raise ValueError("Invalid project_key")


def _query_terms(query: str) -> list[str]:
    return [part for part in re.split(r"[\s,，。！？、；：!?;:]+", query.strip()) if part]


def _candidate_status_for_action(action: MutationAction) -> CandidateStatus:
    if action is MutationAction.REJECT:
        return CandidateStatus.REJECTED
    if action in {MutationAction.REQUIRE_CONFIRMATION, MutationAction.CONFLICT}:
        return CandidateStatus.CONFIRMATION_PENDING
    return CandidateStatus.ACCEPTED


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        project_key=str(row["project_key"]) if row["project_key"] is not None else None,
        scope=MemoryScope(str(row["scope"])),
        kind=MemoryKind(str(row["kind"])),
        content=str(row["content"]),
        summary=str(row["summary"]),
        status=MemoryStatus(str(row["status"])),
        source_session_id=str(row["source_session_id"]),
        content_hash=str(row["content_hash"]),
        version=int(row["version"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        authority=MemoryAuthority(str(row["authority"])),
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        supersedes_memory_id=(
            str(row["supersedes_memory_id"])
            if row["supersedes_memory_id"] is not None
            else None
        ),
        review_state=MemoryReviewState(str(row["review_state"])),
        last_recalled_at=(
            datetime.fromisoformat(str(row["last_recalled_at"]))
            if row["last_recalled_at"] is not None
            else None
        ),
        recall_count=int(row["recall_count"]),
    )


def _row_to_candidate(row: sqlite3.Row) -> StoredMemoryCandidate:
    candidate = MemoryCandidate(
        candidate_id=str(row["candidate_id"]),
        user_id=str(row["user_id"]),
        project_key=str(row["project_key"]) if row["project_key"] is not None else None,
        scope=MemoryScope(str(row["scope"])),
        kind=MemoryKind(str(row["kind"])),
        content=str(row["content"]),
        confidence=float(row["confidence"]),
        evidence_text=str(row["evidence_text"]),
        source_session_id=str(row["source_session_id"]),
        source_turn_id=str(row["source_turn_id"]),
        extraction_run_id=str(row["extraction_run_id"]),
        extractor_model=str(row["extractor_model"]),
        sensitivity=SensitivityDecision(str(row["sensitivity"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )
    return StoredMemoryCandidate(
        candidate=candidate,
        status=CandidateStatus(str(row["status"])),
        reason_code=str(row["reason_code"]) if row["reason_code"] is not None else None,
        decided_at=(
            datetime.fromisoformat(str(row["decided_at"]))
            if row["decided_at"] is not None
            else None
        ),
    )


_MIGRATION_V1 = """
CREATE TABLE memory_entries (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_key TEXT,
    project_key_key TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('user', 'project')),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'archived', 'conflicted', 'deleted')),
    source_session_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, scope, project_key_key, content_hash)
);
CREATE INDEX idx_memory_identity
ON memory_entries(user_id, status, scope, project_key_key);
CREATE VIRTUAL TABLE memory_fts USING fts5(memory_id UNINDEXED, content, summary);
CREATE TABLE memory_index_outbox (
    event_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN ('upsert', 'delete')),
    memory_version INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    leased_until TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE INDEX idx_memory_outbox_pending
ON memory_index_outbox(delivered_at, available_at, leased_until);
"""

_MIGRATION_V2 = """
CREATE TABLE memory_migration_journal (
    source_version INTEGER NOT NULL,
    legacy_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    migrated_at TEXT NOT NULL,
    PRIMARY KEY(source_version, legacy_id, source_path)
);
CREATE INDEX idx_memory_migration_target
ON memory_migration_journal(memory_id);
"""

_MIGRATION_V3 = """
ALTER TABLE memory_entries RENAME TO memory_entries_v2;
CREATE TABLE memory_entries (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_key TEXT,
    project_key_key TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('user', 'project')),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('active', 'archived', 'conflicted', 'superseded', 'deleted')
    ),
    source_session_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    authority TEXT NOT NULL DEFAULT 'explicit' CHECK(
        authority IN ('explicit', 'approved_extraction', 'automatic_extraction', 'migrated')
    ),
    confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    supersedes_memory_id TEXT,
    review_state TEXT NOT NULL DEFAULT 'accepted' CHECK(
        review_state IN ('accepted', 'confirmation_pending', 'conflict_pending')
    ),
    last_recalled_at TEXT,
    recall_count INTEGER NOT NULL DEFAULT 0 CHECK(recall_count >= 0),
    UNIQUE(user_id, scope, project_key_key, content_hash)
);
INSERT INTO memory_entries(
    id, user_id, project_key, project_key_key, scope, kind, content, summary,
    status, source_session_id, content_hash, version, created_at, updated_at
)
SELECT
    id, user_id, project_key, project_key_key, scope, kind, content, summary,
    status, source_session_id, content_hash, version, created_at, updated_at
FROM memory_entries_v2;
DROP TABLE memory_entries_v2;
CREATE INDEX idx_memory_identity
ON memory_entries(user_id, status, scope, project_key_key);

CREATE TABLE memory_extraction_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_key TEXT,
    source_session_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('shadow', 'review', 'automatic')),
    extractor_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    latency_ms REAL NOT NULL DEFAULT 0,
    error_code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, source_session_id, source_turn_id)
);

CREATE TABLE memory_candidates (
    candidate_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_key TEXT,
    project_key_key TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('user', 'project')),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    evidence_text TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    extraction_run_id TEXT NOT NULL,
    extractor_model TEXT NOT NULL,
    sensitivity TEXT NOT NULL CHECK(
        sensitivity IN ('safe', 'sensitive', 'needs_confirmation')
    ),
    normalized_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('pending', 'accepted', 'rejected', 'confirmation_pending')
    ),
    reason_code TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, source_session_id, source_turn_id, normalized_hash)
);
CREATE INDEX idx_memory_candidates_review
ON memory_candidates(user_id, status, created_at);

CREATE TABLE memory_evidence (
    evidence_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    candidate_id TEXT,
    source_session_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_memory_evidence_memory ON memory_evidence(memory_id, created_at);

CREATE TABLE memory_mutation_log (
    mutation_id TEXT PRIMARY KEY,
    candidate_id TEXT,
    memory_id TEXT,
    target_memory_id TEXT,
    action TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    before_version INTEGER,
    after_version INTEGER,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_memory_mutation_memory ON memory_mutation_log(memory_id, created_at);

CREATE TABLE memory_extraction_jobs (
    job_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_key TEXT,
    source_session_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    user_input TEXT NOT NULL,
    assistant_response TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('shadow', 'review', 'automatic')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    leased_until TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(
        status IN ('pending', 'completed', 'failed')
    ),
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(user_id, source_session_id, source_turn_id)
);
CREATE INDEX idx_memory_extraction_jobs_pending
ON memory_extraction_jobs(status, available_at, leased_until);

CREATE TABLE memory_dead_letters (
    dead_letter_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK(source_type IN ('extraction', 'outbox')),
    source_id TEXT NOT NULL,
    memory_id TEXT,
    error_code TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    replayed_at TEXT,
    UNIQUE(source_type, source_id)
);
CREATE INDEX idx_memory_dead_letters_active
ON memory_dead_letters(replayed_at, created_at);
"""

_MIGRATION_V4 = """
CREATE TABLE IF NOT EXISTS memory_dead_letters (
    dead_letter_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK(source_type IN ('extraction', 'outbox')),
    source_id TEXT NOT NULL,
    memory_id TEXT,
    error_code TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    replayed_at TEXT,
    UNIQUE(source_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_dead_letters_active
ON memory_dead_letters(replayed_at, created_at);
"""
