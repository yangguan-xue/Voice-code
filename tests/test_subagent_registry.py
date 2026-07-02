from __future__ import annotations

from voice_code.subagents.events import TaskEventType
from voice_code.subagents.registry import TaskRegistry
from voice_code.subagents.types import (
    AgentTask,
    TaskProgress,
    TaskStatus,
    TaskSummary,
)


def _make_task(task_id: str = "task-1") -> AgentTask:
    return AgentTask(
        task_id=task_id,
        session_id="session-1",
        parent_task_id=None,
        parent_session_id=None,
        agent_type="general-purpose",
        description="Run a child agent",
        prompt="Investigate the problem",
        status=TaskStatus.PENDING,
        model_name="test-model",
        transcript_path=f"/tmp/{task_id}.jsonl",
        created_at=1.0,
    )


def test_registry_create_and_get_task():
    registry = TaskRegistry()
    task = _make_task()

    registry.create_task(task)

    loaded = registry.get_task("task-1")
    assert loaded is not None
    assert loaded.task_id == "task-1"
    assert loaded.status == TaskStatus.PENDING


def test_registry_rejects_duplicate_task_ids():
    registry = TaskRegistry()
    task = _make_task()

    registry.create_task(task)

    try:
        registry.create_task(task)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected duplicate create to fail")


def test_registry_updates_status_and_records_event():
    registry = TaskRegistry()
    registry.create_task(_make_task())

    registry.update_status("task-1", TaskStatus.RUNNING)

    loaded = registry.get_task("task-1")
    assert loaded is not None
    assert loaded.status == TaskStatus.RUNNING
    assert loaded.started_at is not None

    events = registry.drain_events()
    assert [event.type for event in events] == [
        TaskEventType.CREATED,
        TaskEventType.STARTED,
    ]


def test_registry_updates_progress_and_summary():
    registry = TaskRegistry()
    registry.create_task(_make_task())

    registry.update_progress(
        "task-1",
        TaskProgress(
            tool_use_count=3,
            token_count=120,
            last_activity="Reading files",
            summary="Investigating",
        ),
    )
    registry.complete_task(
        "task-1",
        TaskSummary(
            summary="Done",
            transcript_path="/tmp/task-1.jsonl",
            total_tokens=120,
            tool_uses=3,
            duration_ms=2500,
        ),
    )

    loaded = registry.get_task("task-1")
    assert loaded is not None
    assert loaded.status == TaskStatus.COMPLETED
    assert loaded.progress is not None
    assert loaded.progress.tool_use_count == 3
    assert loaded.result_summary == "Done"

    events = registry.drain_events()
    assert [event.type for event in events] == [
        TaskEventType.CREATED,
        TaskEventType.PROGRESS,
        TaskEventType.COMPLETED,
    ]


def test_registry_cancel_marks_abort_requested():
    registry = TaskRegistry()
    registry.create_task(_make_task())

    registry.request_stop("task-1")

    loaded = registry.get_task("task-1")
    assert loaded is not None
    assert loaded.stop_requested is True

    registry.cancel_task("task-1", error="user cancelled")
    loaded = registry.get_task("task-1")
    assert loaded is not None
    assert loaded.status == TaskStatus.CANCELLED
    assert loaded.error == "user cancelled"


def test_registry_lists_tasks_for_session():
    registry = TaskRegistry()
    registry.create_task(_make_task("task-1"))
    registry.create_task(
        AgentTask(
            task_id="task-2",
            session_id="session-2",
            parent_task_id=None,
            parent_session_id=None,
            agent_type="researcher",
            description="Task 2",
            prompt="Prompt 2",
            status=TaskStatus.PENDING,
            model_name=None,
            transcript_path="/tmp/task-2.jsonl",
            created_at=2.0,
        )
    )

    tasks = registry.list_tasks(session_id="session-1")
    assert [task.task_id for task in tasks] == ["task-1"]


def test_registry_restore_tasks_replaces_state_without_new_events():
    registry = TaskRegistry()
    restored_task = _make_task("task-restored")
    restored_task.status = TaskStatus.COMPLETED

    registry.restore_tasks([restored_task])

    tasks = registry.list_tasks(session_id="session-1")
    assert [task.task_id for task in tasks] == ["task-restored"]
    assert registry.drain_events() == []
