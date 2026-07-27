from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator

import pytest

from voice_code.agent.abort import AbortSignal
from voice_code.agent.types import AgentEvent, EventType
from voice_code.desktop.bridge import DesktopAgentBridge
from voice_code.desktop.permissions import DesktopPermissionApprover
from voice_code.desktop.protocol import (
    BridgeEvent,
    PermissionResolvePayload,
    map_agent_event,
)
from voice_code.permissions import (
    PermissionBehavior,
    PermissionDecision,
    PermissionRequest,
)


async def _fake_runner(
    _text: str,
    *,
    abort_signal: AbortSignal,
) -> AsyncIterator[AgentEvent]:
    assert not abort_signal.is_triggered()
    yield AgentEvent(type=EventType.REASONING, turn=1, content="先看目录。")
    yield AgentEvent(type=EventType.TEXT, turn=1, content="我先看项目结构。")
    yield AgentEvent(
        type=EventType.TOOL_CALL,
        turn=1,
        tool_call_id="tool-1",
        tool_name="grep",
        tool_args={"pattern": "TUI"},
    )
    yield AgentEvent(
        type=EventType.TOOL_RESULT,
        turn=1,
        tool_call_id="tool-1",
        tool_name="grep",
        tool_result="docs/specs/tui-goal-session-experience.md",
    )
    yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")


@pytest.mark.asyncio
async def test_desktop_bridge_streams_turn_events() -> None:
    events: list[BridgeEvent] = []
    bridge = DesktopAgentBridge(session_id="session-1", runner=_fake_runner, emit=events.append)

    result = await bridge.start_turn("查看项目结构")

    assert result.turn_id == 1
    assert [event.event for event in events] == [
        "agent.turn.started",
        "agent.reasoning.delta",
        "agent.text.delta",
        "agent.tool.call",
        "agent.tool.result",
        "agent.turn.finish",
    ]
    assert events[0].payload["userText"] == "查看项目结构"
    assert events[2].payload["content"] == "我先看项目结构。"
    assert events[4].payload["resultPreview"] == "docs/specs/tui-goal-session-experience.md"


@pytest.mark.asyncio
async def test_desktop_bridge_remaps_internal_agent_turns_to_desktop_turns() -> None:
    async def runner(
        text: str,
        *,
        abort_signal: AbortSignal,
    ) -> AsyncIterator[AgentEvent]:
        assert not abort_signal.is_triggered()
        yield AgentEvent(type=EventType.TEXT, turn=1, content=f"回复：{text}")
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

    events: list[BridgeEvent] = []
    bridge = DesktopAgentBridge(session_id="session-1", runner=runner, emit=events.append)

    first = await bridge.start_turn("你好")
    second = await bridge.start_turn("你是什么模型")

    assert first.turn_id == 1
    assert second.turn_id == 2
    assert [
        (event.event, event.turn_id, event.payload.get("content") or event.payload.get("userText"))
        for event in events
    ] == [
        ("agent.turn.started", 1, "你好"),
        ("agent.text.delta", 1, "回复：你好"),
        ("agent.turn.finish", 1, None),
        ("agent.turn.started", 2, "你是什么模型"),
        ("agent.text.delta", 2, "回复：你是什么模型"),
        ("agent.turn.finish", 2, None),
    ]


@pytest.mark.asyncio
async def test_desktop_bridge_interrupt_triggers_abort_signal() -> None:
    started = asyncio.Event()
    released = asyncio.Event()

    async def runner(
        _text: str,
        *,
        abort_signal: AbortSignal,
    ) -> AsyncIterator[AgentEvent]:
        started.set()
        while not abort_signal.is_triggered():
            await asyncio.sleep(0.001)
        released.set()
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="interrupted")

    bridge = DesktopAgentBridge(session_id="session-1", runner=runner)
    task = asyncio.create_task(bridge.start_turn("停下来测试"))
    await started.wait()

    assert bridge.interrupt_turn(turn_id=1)

    await asyncio.wait_for(released.wait(), timeout=1)
    result = await task
    assert result.finish_reason == "interrupted"


def test_map_agent_event_uses_stable_desktop_names() -> None:
    event = AgentEvent(
        type=EventType.TOOL_CALL,
        turn=3,
        tool_call_id="call-1",
        tool_name="bash",
        tool_args={"command": "git status"},
    )

    mapped = map_agent_event("session-1", event)

    assert mapped.event == "agent.tool.call"
    assert mapped.turn_id == 3
    assert mapped.payload == {
        "toolCallId": "call-1",
        "toolName": "bash",
        "toolArgs": {"command": "git status"},
    }


def test_map_agent_event_maps_tool_errors_to_tool_results() -> None:
    event = AgentEvent(
        type=EventType.ERROR,
        turn=3,
        content="<tool_use_error>Error: use glob instead</tool_use_error>",
        status="generic_error",
        tool_call_id="call-1",
        tool_name="bash",
    )

    mapped = map_agent_event("session-1", event)

    assert mapped.event == "agent.tool.result"
    assert mapped.turn_id == 3
    assert mapped.payload == {
        "toolCallId": "call-1",
        "toolName": "bash",
        "toolResult": "<tool_use_error>Error: use glob instead</tool_use_error>",
        "resultPreview": "Error: use glob instead",
        "status": "error",
    }


def test_desktop_permission_approver_waits_for_ui_resolution() -> None:
    emitted: list[BridgeEvent] = []
    approver = DesktopPermissionApprover(emit=emitted.append, timeout_seconds=1)
    request = PermissionRequest(
        tool_name="write",
        tool_input={"file_path": "/tmp/example.txt", "content": "hello"},
        reason="Non-readonly tools require approval by default.",
        session_id="session-1",
        risk_category="medium",
    )

    result: list[PermissionDecision] = []
    thread = threading.Thread(target=lambda: result.append(approver.approve(request)))
    thread.start()

    assert _wait_until(lambda: bool(emitted))
    request_id = str(emitted[0].payload["requestId"])
    approver.resolve(
        PermissionResolvePayload(
            request_id=request_id,
            behavior="allow",
            remember_scope="session",
        )
    )
    thread.join(timeout=1)

    assert result
    assert result[0].behavior == PermissionBehavior.ALLOW
    assert result[0].remember_scope == "session"
    assert emitted[0].event == "permission.request"
    assert emitted[0].payload["toolName"] == "write"


def test_desktop_permission_approver_denies_pending_on_disconnect() -> None:
    emitted: list[BridgeEvent] = []
    approver = DesktopPermissionApprover(emit=emitted.append, timeout_seconds=5)
    request = PermissionRequest(tool_name="bash", tool_input={"command": "rm -rf /tmp/nope"})

    result: list[PermissionDecision] = []
    thread = threading.Thread(target=lambda: result.append(approver.approve(request)))
    thread.start()

    assert _wait_until(lambda: bool(emitted))
    approver.deny_all_pending("UI disconnected.")
    thread.join(timeout=1)

    assert result
    assert result[0].behavior == PermissionBehavior.DENY
    assert result[0].message == "UI disconnected."


def _wait_until(predicate, *, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        threading.Event().wait(0.001)
    return False
