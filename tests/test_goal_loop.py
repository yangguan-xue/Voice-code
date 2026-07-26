from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from voice_code.goals import (
    GoalLoop,
    GoalMigrationRequiredError,
    GoalSpec,
    GoalStateConflictError,
    GoalStore,
)
from voice_code.goals.types import GoalState, GoalStatus
from voice_code.goals.verifier import run_verification_command


def _spec(tmp_path: Path, *, goal_id: str = "goal-test", **overrides: object) -> GoalSpec:
    values: dict[str, object] = {
        "goal_id": goal_id,
        "objective": "make the checks pass",
        "workspace": str(tmp_path),
        "verification_commands": ["true"],
        "require_review": False,
    }
    values.update(overrides)
    return GoalSpec(**values)  # type: ignore[arg-type]


def test_store_create_refuses_to_overwrite_an_existing_goal(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    store = GoalStore(tmp_path)
    original = GoalState(goal_id=spec.goal_id, iteration=3)
    store.create(spec, original)

    with pytest.raises(FileExistsError):
        store.create(spec, GoalState(goal_id=spec.goal_id))

    assert store.load_state(spec.goal_id).iteration == 3


def test_loading_an_unknown_goal_does_not_create_a_phantom_directory(tmp_path: Path) -> None:
    store = GoalStore(tmp_path)

    with pytest.raises(FileNotFoundError):
        store.load_state("missing-goal")

    assert not (store.root / "missing-goal").exists()


def test_store_rejects_mismatched_spec_and_state_ids(tmp_path: Path) -> None:
    store = GoalStore(tmp_path)
    spec = _spec(tmp_path, goal_id="spec-id")

    with pytest.raises(ValueError, match="goal IDs must match"):
        store.create(spec, GoalState(goal_id="state-id"))

    assert not (store.root / "spec-id").exists()
    assert not (store.root / "state-id").exists()


def test_store_rejects_a_stale_state_sequence(tmp_path: Path) -> None:
    spec = GoalSpec("goal-cas", "test CAS", str(tmp_path), ["true"])
    store = GoalStore(tmp_path)
    store.create(spec, GoalState(goal_id=spec.goal_id))
    first = store.load_state(spec.goal_id)
    stale = store.load_state(spec.goal_id)

    first.iteration = 1
    store.save_state(first)
    stale.iteration = 2

    with pytest.raises(GoalStateConflictError, match="expected sequence 0.*found 1"):
        store.save_state(stale)


def test_store_writes_hash_chained_redacted_events(tmp_path: Path) -> None:
    spec = GoalSpec("goal-events", "audit state", str(tmp_path), ["true"])
    store = GoalStore(tmp_path)
    state = GoalState(goal_id=spec.goal_id)
    store.create(spec, state)
    state.last_result = "Authorization: Bearer live-secret"

    store.save_state(state)
    events = store.load_events(spec.goal_id)

    assert [event["sequence"] for event in events] == [0, 1]
    assert events[0]["event_type"] == "goal_created"
    assert events[1]["prev_hash"] == events[0]["event_hash"]
    assert events[1]["state"]["last_result"] == "Authorization: Bearer [REDACTED]"


def test_store_fails_closed_when_an_event_is_tampered(tmp_path: Path) -> None:
    spec = GoalSpec("goal-event-tamper", "audit state", str(tmp_path), ["true"])
    store = GoalStore(tmp_path)
    store.create(spec, GoalState(goal_id=spec.goal_id))
    event_path = store.goal_dir(spec.goal_id) / "events" / "00000000000000000000.json"
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    payload["to_status"] = "completed"
    event_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="event hash mismatch"):
        store.load_state(spec.goal_id)


def test_store_recovers_materialized_state_from_a_committed_event(tmp_path: Path) -> None:
    spec = GoalSpec("goal-event-recover", "recover state", str(tmp_path), ["true"])
    store = GoalStore(tmp_path)
    state = GoalState(goal_id=spec.goal_id)
    store.create(spec, state)
    state_path = store.goal_dir(spec.goal_id) / "state.json"
    original_state = state_path.read_text(encoding="utf-8")
    state.iteration = 1
    store.save_state(state)
    state_path.write_text(original_state, encoding="utf-8")

    recovered = store.load_state(spec.goal_id)

    assert recovered.sequence == 1
    assert recovered.iteration == 1


def test_store_recreates_a_missing_materialized_state_from_events(tmp_path: Path) -> None:
    spec = GoalSpec("goal-state-missing", "recover state", str(tmp_path), ["true"])
    store = GoalStore(tmp_path)
    store.create(spec, GoalState(goal_id=spec.goal_id, iteration=2))
    (store.goal_dir(spec.goal_id) / "state.json").unlink()

    recovered = store.load_state(spec.goal_id)

    assert recovered.sequence == 0
    assert recovered.iteration == 2


def test_store_refuses_to_mutate_a_legacy_goal_without_events(tmp_path: Path) -> None:
    spec = GoalSpec("goal-legacy", "legacy state", str(tmp_path), ["true"])
    store = GoalStore(tmp_path)
    directory = store.goal_dir(spec.goal_id)
    directory.mkdir(mode=0o700)
    (directory / "goal.json").write_text(json.dumps(spec.to_dict()), encoding="utf-8")
    (directory / "state.json").write_text(
        json.dumps(GoalState(goal_id=spec.goal_id).to_dict()), encoding="utf-8"
    )
    state = store.load_state(spec.goal_id)

    with pytest.raises(GoalMigrationRequiredError, match="migration"):
        store.save_state(state)


def test_store_rejects_unknown_persisted_schema_versions(tmp_path: Path) -> None:
    spec = GoalSpec("goal-schema", "validate schema", str(tmp_path), ["true"])
    store = GoalStore(tmp_path)
    directory = store.goal_dir(spec.goal_id)
    directory.mkdir(mode=0o700)
    spec_payload = spec.to_dict()
    spec_payload["schema_version"] = 99
    (directory / "goal.json").write_text(json.dumps(spec_payload), encoding="utf-8")
    state_payload = GoalState(goal_id=spec.goal_id).to_dict()
    state_payload["schema_version"] = 99
    (directory / "state.json").write_text(json.dumps(state_payload), encoding="utf-8")

    with pytest.raises(ValueError, match="goal spec schema version 99"):
        store.load_spec(spec.goal_id)
    with pytest.raises(ValueError, match="goal state schema version 99"):
        store.load_state(spec.goal_id)


def test_store_redacts_secrets_before_persisting_evidence(tmp_path: Path) -> None:
    store = GoalStore(tmp_path)

    name = store.write_evidence(
        "goal-redact",
        1,
        {
            "stdout": "Authorization: Bearer live-token",
            "nested": {"api_key": "api_key=top-secret"},
        },
    )

    content = (store.goal_dir("goal-redact") / "evidence" / name).read_text(
        encoding="utf-8"
    )
    assert "live-token" not in content
    assert "top-secret" not in content
    assert content.count("[REDACTED]") == 2


def test_store_never_overwrites_existing_iteration_evidence(tmp_path: Path) -> None:
    store = GoalStore(tmp_path)

    first = store.write_evidence("goal-evidence", 1, {"attempt": 1})
    second = store.write_evidence("goal-evidence", 1, {"attempt": 2})

    assert first == "iteration-001.json"
    assert second == "iteration-001-attempt-002.json"
    evidence_dir = store.goal_dir("goal-evidence") / "evidence"
    assert '"attempt": 1' in (evidence_dir / first).read_text(encoding="utf-8")
    assert '"attempt": 2' in (evidence_dir / second).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_required_reviewer_cannot_be_silently_skipped(tmp_path: Path) -> None:
    spec = _spec(tmp_path, require_review=True)

    async def builder(_prompt: str) -> str:
        return "done"

    with pytest.raises(ValueError, match="reviewer is required"):
        await GoalLoop(spec).run(builder)

    assert not (tmp_path / ".reasoning" / "goals" / spec.goal_id).exists()


@pytest.mark.asyncio
async def test_builder_exception_persists_failed_terminal_state(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    async def builder(_prompt: str) -> str:
        raise RuntimeError("builder exploded")

    with pytest.raises(RuntimeError, match="builder exploded"):
        await GoalLoop(spec).run(builder)

    state = GoalStore(tmp_path).load_state(spec.goal_id)
    assert state.status == GoalStatus.FAILED
    assert state.history[-1]["reason"] == "builder exploded"


@pytest.mark.asyncio
async def test_completed_goal_event_references_its_decision_evidence(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    async def builder(_prompt: str) -> str:
        return "done"

    result = await GoalLoop(spec).run(builder)
    events = GoalStore(tmp_path).load_events(spec.goal_id)

    assert result.state.status == GoalStatus.COMPLETED
    assert events[-1]["to_status"] == "completed"
    assert events[-1]["reason_code"] == "FINISH"
    assert events[-1]["evidence_refs"] == ["iteration-001.json"]
    assert events[-1]["sequence"] == result.state.sequence


@pytest.mark.asyncio
async def test_goal_deadline_stops_a_running_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec(tmp_path, max_seconds=60)
    loop = GoalLoop(spec)
    remaining = iter((60.0, 0.01))
    monkeypatch.setattr(loop, "_remaining_seconds", lambda _state: next(remaining))
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def builder(_prompt: str) -> str:
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
        return "unreachable"

    result = await loop.run(builder)

    assert result.state.status == GoalStatus.STOPPED
    assert result.message == "time budget exhausted"
    assert started.is_set()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_resume_reverifies_an_interrupted_iteration_before_rebuilding(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    store = GoalStore(tmp_path)
    store.create(
        spec,
        GoalState(
            goal_id=spec.goal_id,
            status=GoalStatus.VERIFYING,
            iteration=1,
            started_at=time.time(),
            updated_at=time.time(),
            last_prompt=spec.objective,
            last_result="builder already finished",
        ),
    )
    builder_called = False

    async def builder(_prompt: str) -> str:
        nonlocal builder_called
        builder_called = True
        return "should not run"

    result = await GoalLoop(spec).resume(builder)

    assert result.state.status == GoalStatus.COMPLETED
    assert result.state.iteration == 1
    assert builder_called is False


@pytest.mark.asyncio
async def test_completed_goal_cannot_be_resumed(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    GoalStore(tmp_path).create(
        spec,
        GoalState(goal_id=spec.goal_id, status=GoalStatus.COMPLETED),
    )

    async def builder(_prompt: str) -> str:
        return "unused"

    with pytest.raises(ValueError, match="cannot resume goal in completed state"):
        await GoalLoop(spec).resume(builder)


@pytest.mark.asyncio
async def test_resume_rejects_a_changed_goal_spec(tmp_path: Path) -> None:
    original = _spec(tmp_path)
    GoalStore(tmp_path).create(original, GoalState(goal_id=original.goal_id))
    changed = _spec(tmp_path, objective="weaken the objective")

    async def builder(_prompt: str) -> str:
        return "unused"

    with pytest.raises(ValueError, match="does not match persisted specification"):
        await GoalLoop(changed).resume(builder)


@pytest.mark.asyncio
async def test_resume_compares_specs_after_secret_redaction(tmp_path: Path) -> None:
    spec = _spec(tmp_path, objective="rotate api_key=top-secret")
    GoalStore(tmp_path).create(
        spec,
        GoalState(
            goal_id=spec.goal_id,
            started_at=time.time(),
            updated_at=time.time(),
        ),
    )

    async def builder(_prompt: str) -> str:
        return "done"

    result = await GoalLoop(spec).resume(builder)

    assert result.state.status == GoalStatus.COMPLETED


@pytest.mark.asyncio
async def test_cancelling_verification_kills_its_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "verifier.pid"
    command = _shell_join(
        [
            sys.executable,
            "-c",
            (
                "import os, pathlib, time; "
                f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()), "
                "encoding='utf-8'); "
                "time.sleep(10)"
            ),
        ]
    )
    task = asyncio.create_task(
        run_verification_command(
            command,
            workspace=tmp_path,
            timeout_seconds=10,
        )
    )
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.005)
    pid = int(pid_file.read_text(encoding="utf-8"))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    try:
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                break
            await asyncio.sleep(0.005)
        else:
            pytest.fail("verification process survived task cancellation")
    finally:
        _kill_test_process_group(pid)


def _shell_join(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return " ".join(shlex.quote(arg) for arg in args)


def _kill_test_process_group(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
