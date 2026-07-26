"""Atomic local persistence for long-running goal loops."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import time
import uuid
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from voice_code.audit import record_audit_event
from voice_code.goals.types import GoalSpec, GoalState, GoalStatus
from voice_code.platform_fs import best_effort_private_permissions
from voice_code.security import redact_secrets

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised via Windows import smoke
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX platforms
    msvcrt = None  # type: ignore[assignment]


class GoalLeaseConflictError(RuntimeError):
    """Raised when another process owns the goal's execution lease."""


class GoalStateConflictError(RuntimeError):
    """Raised when a stale materialized state attempts to overwrite a newer state."""


class GoalMigrationRequiredError(RuntimeError):
    """Raised when a legacy goal must be migrated before it can be modified."""


class GoalLease(AbstractContextManager["GoalLease"]):
    """Advisory process lease released automatically on process exit."""

    def __init__(self, path: Path, goal_id: str) -> None:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        self._fd = os.open(path, flags, 0o600)
        self._released = False
        try:
            self._lock()
        except (BlockingIOError, OSError) as exc:
            os.close(self._fd)
            self._released = True
            raise GoalLeaseConflictError(f"goal {goal_id} is already running") from exc
        metadata = json.dumps(
            {"pid": os.getpid(), "host": socket.gethostname(), "acquired_at": time.time()}
        ).encode()
        os.ftruncate(self._fd, 0)
        os.write(self._fd, metadata)
        os.fsync(self._fd)

    def __exit__(self, *exc_info: object) -> None:
        if self._released:
            return
        self._unlock()
        os.close(self._fd)
        self._released = True

    def _lock(self) -> None:
        if fcntl is not None:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        if msvcrt is None:
            return
        os.lseek(self._fd, 0, os.SEEK_SET)
        msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)

    def _unlock(self) -> None:
        if fcntl is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            return
        if msvcrt is None:
            return
        os.lseek(self._fd, 0, os.SEEK_SET)
        msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)


class GoalStore:
    def __init__(self, workspace: str | Path) -> None:
        self.root = Path(workspace).resolve() / ".reasoning" / "goals"
        self.root.mkdir(parents=True, exist_ok=True)
        best_effort_private_permissions(self.root, directory=True)

    def goal_dir(self, goal_id: str) -> Path:
        return self._goal_path(goal_id)

    def _goal_path(self, goal_id: str) -> Path:
        if not goal_id or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in goal_id):
            raise ValueError("goal_id contains unsupported characters")
        return self.root / goal_id

    def create(self, spec: GoalSpec, state: GoalState) -> None:
        if spec.goal_id != state.goal_id:
            raise ValueError("goal IDs must match")
        if state.sequence != 0:
            raise ValueError("new goal state must start at sequence 0")
        directory = self._goal_path(spec.goal_id)
        directory.mkdir(mode=0o700)
        self._write_json(directory / "goal.json", spec.to_dict())
        event = self._build_event(
            state.to_dict(),
            sequence=0,
            event_type="goal_created",
            from_status="",
            prev_hash="",
        )
        self._write_event(spec.goal_id, event)
        self._write_json(directory / "state.json", event["state"])
        self.write_progress(spec, state)

    def save_state(
        self,
        state: GoalState,
        *,
        reason_code: str = "",
        evidence_refs: list[str] | None = None,
    ) -> None:
        current = self._load_state_payload(state.goal_id)
        current_sequence = int(current.get("sequence", 0))
        if current_sequence != state.sequence:
            raise GoalStateConflictError(
                f"goal {state.goal_id} expected sequence {state.sequence}, "
                f"found {current_sequence}"
            )
        next_sequence = current_sequence + 1
        next_state = state.to_dict()
        next_state["sequence"] = next_sequence
        events = self.load_events(state.goal_id)
        if not events:
            raise GoalMigrationRequiredError(
                f"goal {state.goal_id} requires event-log migration before mutation"
            )
        previous = events[-1]
        event = self._build_event(
            next_state,
            sequence=next_sequence,
            event_type=(
                "status_changed"
                if current.get("status") != next_state.get("status")
                else "state_updated"
            ),
            from_status=str(current.get("status", "")),
            prev_hash=str(previous.get("event_hash", "")),
            reason_code=reason_code,
            evidence_refs=evidence_refs or [],
        )
        try:
            self._write_event(state.goal_id, event)
        except FileExistsError as exc:
            found = int(self._load_state_payload(state.goal_id).get("sequence", 0))
            raise GoalStateConflictError(
                f"goal {state.goal_id} expected sequence {state.sequence}, found {found}"
            ) from exc
        self._write_json(self.goal_dir(state.goal_id) / "state.json", event["state"])
        record_audit_event(
            event_type="goal.state_changed",
            actor="agent",
            resource_id=f"goal:{state.goal_id}",
            outcome=str(event["event_type"]),
            rule=reason_code or "goal_state_store",
            approval_result="recorded",
        )
        state.sequence = next_sequence

    def write_evidence(self, goal_id: str, iteration: int, payload: dict[str, Any]) -> str:
        directory = self.goal_dir(goal_id) / "evidence"
        directory.mkdir(parents=True, exist_ok=True)
        name = f"iteration-{iteration:03d}.json"
        attempt = 2
        while (directory / name).exists():
            name = f"iteration-{iteration:03d}-attempt-{attempt:03d}.json"
            attempt += 1
        self._write_json(directory / name, payload)
        return name

    def write_progress(self, spec: GoalSpec, state: GoalState) -> None:
        lines = [
            f"# Goal {spec.goal_id}",
            "",
            f"- Objective: {spec.objective}",
            f"- Status: {state.status}",
            f"- Iteration: {state.iteration}/{spec.max_iterations}",
            f"- Last decision: {state.history[-1].get('decision', '') if state.history else ''}",
        ]
        content = str(redact_secrets("\n".join(lines) + "\n"))
        self._atomic_write(self.goal_dir(spec.goal_id) / "progress.md", content)

    def load_spec(self, goal_id: str) -> GoalSpec:
        payload = self._read_json(self.goal_dir(goal_id) / "goal.json")
        schema_version = int(payload.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(f"unsupported goal spec schema version {schema_version}")
        return GoalSpec(**payload)

    def load_state(self, goal_id: str) -> GoalState:
        payload = self._load_state_payload(goal_id)
        if (self.goal_dir(goal_id) / "stop.requested").is_file():
            payload["stop_requested"] = True
        payload["status"] = GoalStatus(payload.get("status", GoalStatus.CREATED))
        return GoalState(**payload)

    def load_events(self, goal_id: str) -> list[dict[str, Any]]:
        directory = self.goal_dir(goal_id) / "events"
        if not directory.exists():
            return []
        events: list[dict[str, Any]] = []
        previous_hash = ""
        for expected_sequence, path in enumerate(sorted(directory.glob("*.json"))):
            expected_name = f"{expected_sequence:020d}.json"
            if path.name != expected_name:
                raise ValueError(f"unexpected goal event filename: {path.name}")
            event = self._read_json(path)
            if event.get("schema_version") != 1:
                raise ValueError(f"unsupported goal event schema in {path}")
            if event.get("sequence") != expected_sequence:
                raise ValueError(f"goal event sequence mismatch in {path}")
            if event.get("prev_hash") != previous_hash:
                raise ValueError(f"goal event previous hash mismatch in {path}")
            claimed_hash = str(event.get("event_hash", ""))
            unhashed = {key: value for key, value in event.items() if key != "event_hash"}
            actual_hash = self._hash_payload(unhashed)
            if claimed_hash != actual_hash:
                raise ValueError(f"goal event hash mismatch in {path}")
            if event.get("goal_id") != goal_id:
                raise ValueError(f"goal event ID mismatch in {path}")
            previous_hash = claimed_hash
            events.append(event)
        return events

    def request_stop(self, goal_id: str) -> GoalState:
        state = self.load_state(goal_id)
        self._atomic_write(
            self.goal_dir(goal_id) / "stop.requested",
            json.dumps({"requested_at": time.time()}) + "\n",
        )
        state.stop_requested = True
        return state

    def is_stop_requested(self, goal_id: str) -> bool:
        if (self.goal_dir(goal_id) / "stop.requested").is_file():
            return True
        return self.load_state(goal_id).stop_requested

    def acquire_lease(self, goal_id: str) -> GoalLease:
        directory = self.goal_dir(goal_id)
        if not directory.is_dir():
            raise FileNotFoundError(f"goal not found: {goal_id}")
        return GoalLease(directory / "execution.lock", goal_id)

    def _load_state_payload(self, goal_id: str) -> dict[str, Any]:
        path = self.goal_dir(goal_id) / "state.json"
        events = self.load_events(goal_id)
        if not events:
            payload = self._read_json(path)
            self._validate_state_payload(payload, goal_id)
            return payload
        latest_state = events[-1].get("state")
        if not isinstance(latest_state, dict):
            raise ValueError("latest goal event does not contain a state snapshot")
        self._validate_state_payload(latest_state, goal_id)
        if not path.exists():
            self._write_json(path, latest_state)
            return dict(latest_state)
        payload = self._read_json(path)
        self._validate_state_payload(payload, goal_id)
        state_sequence = int(payload.get("sequence", 0))
        event_sequence = int(events[-1]["sequence"])
        if state_sequence > event_sequence:
            raise ValueError("materialized goal state is ahead of the event log")
        if state_sequence < event_sequence:
            self._write_json(path, latest_state)
            return dict(latest_state)
        if payload != latest_state:
            raise ValueError("materialized goal state does not match the event log")
        return payload

    @staticmethod
    def _validate_state_payload(payload: dict[str, Any], goal_id: str) -> None:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(f"unsupported goal state schema version {schema_version}")
        if payload.get("goal_id") != goal_id:
            raise ValueError("materialized goal state has a mismatched goal ID")
        sequence = payload.get("sequence", 0)
        if not isinstance(sequence, int) or sequence < 0:
            raise ValueError("materialized goal state has an invalid sequence")

    def _build_event(
        self,
        state: dict[str, Any],
        *,
        sequence: int,
        event_type: str,
        from_status: str,
        prev_hash: str,
        reason_code: str = "",
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        safe_state = redact_secrets(state)
        if not isinstance(safe_state, dict):
            raise TypeError("goal state must serialize to an object")
        event = {
            "schema_version": 1,
            "event_id": f"evt_{uuid.uuid4().hex}",
            "operation_id": f"op_{uuid.uuid4().hex}",
            "goal_id": str(safe_state["goal_id"]),
            "sequence": sequence,
            "event_type": event_type,
            "from_status": from_status,
            "to_status": str(safe_state.get("status", "")),
            "reason_code": reason_code,
            "iteration": int(safe_state.get("iteration", 0)),
            "attempt": 1,
            "actor": "controller",
            "timestamp": datetime.now(UTC).isoformat(),
            "evidence_refs": evidence_refs or [],
            "policy_revision": 0,
            "spec_revision": 1,
            "prev_hash": prev_hash,
            "state": safe_state,
        }
        event["event_hash"] = self._hash_payload(event)
        return event

    def _write_event(self, goal_id: str, event: dict[str, Any]) -> None:
        directory = self.goal_dir(goal_id) / "events"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / f"{int(event['sequence']):020d}.json"
        self._write_new_json(path, event)

    @staticmethod
    def _hash_payload(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Expected object in {path}")
        return payload

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        safe_payload = redact_secrets(payload)
        self._atomic_write(path, json.dumps(safe_payload, ensure_ascii=False, indent=2) + "\n")

    def _write_new_json(self, path: Path, payload: dict[str, Any]) -> None:
        safe_payload = redact_secrets(payload)
        content = json.dumps(safe_payload, ensure_ascii=False, indent=2) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            best_effort_private_permissions(temp_path)
            os.link(temp_path, path, follow_symlinks=False)
            self._fsync_directory(path.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            best_effort_private_permissions(temp_path)
            temp_path.replace(path)
            GoalStore._fsync_directory(path.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            return
        finally:
            os.close(fd)
