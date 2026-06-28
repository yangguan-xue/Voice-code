"""Runtime state helpers for TUI query execution."""

from __future__ import annotations

from dataclasses import dataclass, field

from voice_code.subagents.types import AgentTask, TaskStatus


@dataclass
class QueryRuntimeState:
    """Mutable runtime state for a single TUI query."""

    current_phase: str = ""
    streaming_text: str = ""
    streaming_thinking: str = ""
    last_thinking_block_id: str | None = None
    latest_bash_output_uuid: str | None = None
    live_thinking_text: str = ""
    executing_tools: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeDisplay:
    """Renderable view derived from query runtime state."""

    thinking_text: str = ""
    status_phase: str = ""
    tool_count: int = 0


@dataclass(frozen=True)
class TaskDisplay:
    """Compact task summary for the TUI status area."""

    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0

    @property
    def total(self) -> int:
        return self.running + self.completed + self.failed + self.cancelled


def phase_label(phase: str) -> str:
    """Return a user-facing label for a runtime phase."""
    labels = {
        "thinking": "Thinking…",
        "compacting": "Compacting…",
        "fallback": "Retrying with fallback model…",
        "resuming": "Resuming after token limit…",
    }
    return labels.get(phase, phase)


def build_runtime_display(state: QueryRuntimeState) -> RuntimeDisplay:
    """Summarize the current runtime state into a renderable display shape."""
    if state.live_thinking_text.strip():
        return RuntimeDisplay(
            thinking_text=state.live_thinking_text,
            status_phase=state.current_phase or "thinking",
            tool_count=len(state.executing_tools),
        )

    if state.executing_tools:
        names = list(state.executing_tools.values())
        if len(names) == 1:
            label = f"Executing: {names[0]}"
        else:
            label = f"Executing {len(names)} tools: {', '.join(names[:3])}"
            if len(names) > 3:
                label += f" +{len(names) - 3} more"
        return RuntimeDisplay(
            thinking_text=label,
            status_phase=state.current_phase or "executing",
            tool_count=len(state.executing_tools),
        )

    if not state.current_phase:
        return RuntimeDisplay()

    return RuntimeDisplay(
        thinking_text=phase_label(state.current_phase),
        status_phase=state.current_phase,
        tool_count=0,
    )


def build_task_display(tasks: list[AgentTask]) -> TaskDisplay:
    """Summarize subagent tasks for the status bar."""
    running = 0
    completed = 0
    failed = 0
    cancelled = 0
    for task in tasks:
        if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.WAITING_PERMISSION}:
            running += 1
        elif task.status == TaskStatus.COMPLETED:
            completed += 1
        elif task.status == TaskStatus.FAILED:
            failed += 1
        elif task.status == TaskStatus.CANCELLED:
            cancelled += 1
    return TaskDisplay(
        running=running,
        completed=completed,
        failed=failed,
        cancelled=cancelled,
    )
