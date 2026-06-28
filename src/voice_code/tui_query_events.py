"""Pure query-event state transitions for the TUI."""

from __future__ import annotations

from dataclasses import dataclass

from voice_code.agent.types import AgentEvent, EventType
from voice_code.tui_runtime import QueryRuntimeState


@dataclass(frozen=True)
class TextEventUpdate:
    """Result of applying a streaming text chunk."""

    pending_text: str
    visible_text: str
    output_chars: int


@dataclass(frozen=True)
class BufferCommit:
    """Result of flushing the buffered streaming text."""

    visible_text: str = ""
    thinking_text: str = ""


@dataclass(frozen=True)
class NonStreamOutcome:
    """Result of applying a non-streaming runtime event."""

    kind: str = "noop"


def extract_think_text(text: str) -> tuple[str, str]:
    """Split visible text from embedded <think>...</think> content."""
    if not text:
        return "", ""

    visible_parts: list[str] = []
    think_parts: list[str] = []
    cursor = 0

    while cursor < len(text):
        start = text.find("<think>", cursor)
        if start == -1:
            visible_parts.append(text[cursor:])
            break

        visible_parts.append(text[cursor:start])
        think_start = start + len("<think>")
        end = text.find("</think>", think_start)
        if end == -1:
            think_parts.append(text[think_start:])
            break

        think_parts.append(text[think_start:end])
        cursor = end + len("</think>")

    visible_text = "".join(visible_parts).strip()
    think_text = "\n\n".join(part.strip() for part in think_parts if part.strip()).strip()
    return visible_text, think_text


def apply_text_event(
    state: QueryRuntimeState,
    *,
    pending_text: str,
    content: str,
) -> TextEventUpdate:
    """Apply a TEXT chunk to runtime state and pending buffer."""
    next_pending = pending_text + content
    visible_text, think_text = extract_think_text(next_pending)
    state.streaming_text = visible_text
    state.streaming_thinking = think_text
    state.live_thinking_text = think_text
    return TextEventUpdate(
        pending_text=next_pending,
        visible_text=visible_text,
        output_chars=len(content),
    )


def apply_reasoning_event(state: QueryRuntimeState, content: str) -> None:
    """Apply a REASONING chunk to runtime state."""
    state.live_thinking_text += content
    state.streaming_thinking = state.live_thinking_text


def commit_streaming_buffer(state: QueryRuntimeState, buffer: str) -> BufferCommit:
    """Flush the pending streaming buffer into finalized visible/reasoning text."""
    if not buffer:
        return BufferCommit()

    visible_text, think_text = extract_think_text(buffer)
    state.live_thinking_text = ""
    state.streaming_text = ""
    state.streaming_thinking = ""
    return BufferCommit(visible_text=visible_text, thinking_text=think_text)


def apply_nonstream_event(state: QueryRuntimeState, event: AgentEvent) -> NonStreamOutcome:
    """Apply a non-streaming event to runtime state."""
    if event.type == EventType.TOOL_CALL:
        state.live_thinking_text = ""
        state.streaming_text = ""
        state.streaming_thinking = ""
        state.current_phase = "executing"
        state.executing_tools[event.tool_call_id] = event.tool_name
        return NonStreamOutcome(kind="tool_call")

    if event.type == EventType.TOOL_RESULT:
        state.executing_tools.pop(event.tool_call_id, None)
        state.latest_bash_output_uuid = event.tool_call_id
        state.live_thinking_text = ""
        state.streaming_thinking = ""
        if state.executing_tools:
            state.current_phase = "executing"
        else:
            state.current_phase = "thinking"
        return NonStreamOutcome(kind="tool_result")

    if event.type == EventType.ERROR:
        if event.tool_call_id:
            state.executing_tools.pop(event.tool_call_id, None)
        state.live_thinking_text = ""
        state.streaming_text = ""
        state.streaming_thinking = ""
        state.current_phase = event.phase or "error"
        return NonStreamOutcome(kind="error")

    if event.type == EventType.FINISH:
        state.executing_tools.clear()
        state.current_phase = ""
        state.live_thinking_text = ""
        state.streaming_text = ""
        state.streaming_thinking = ""
        state.last_thinking_block_id = None
        return NonStreamOutcome(kind="finish")

    return NonStreamOutcome()
