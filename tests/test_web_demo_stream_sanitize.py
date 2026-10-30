"""Streaming sanitize tests: sandbox paths split across delta events must not leak."""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_code.desktop.protocol import BridgeEvent
from voice_code.web_demo.handlers import attach_session

SANDBOX = Path("D:/Temp/sandboxes/demo_stream")


class _FakeSandbox:
    path = SANDBOX


class _FakeSession:
    def __init__(self) -> None:
        self.sandbox = _FakeSandbox()
        self._emit = None

    def set_emit(self, emit):
        self._emit = emit


class _FakeSender:
    client_id = "test"

    def __init__(self) -> None:
        self.events: list[BridgeEvent] = []

    async def send(self, message: str) -> None:
        pass

    async def emit(self, event: BridgeEvent) -> None:
        self.events.append(event)


def _delta(text: str, *, event: str = "agent.text.delta") -> BridgeEvent:
    return BridgeEvent(event=event, session_id="demo_stream", turn_id=1, payload={"content": text})


async def _emit(session: _FakeSession, event: BridgeEvent) -> None:
    await session._emit(event)


@pytest.mark.asyncio
async def test_split_sandbox_path_is_sanitized_across_text_deltas() -> None:
    session = _FakeSession()
    sender = _FakeSender()
    attach_session(session, sender, set())

    full = str(SANDBOX) + "\\output\\cat.png"
    mid = len(full) // 2
    await _emit(session, _delta("保存在 " + full[:mid]))
    await _emit(session, _delta(full[mid:] + " 完成"))
    finish = BridgeEvent(
        event="agent.turn.finish",
        session_id="demo_stream",
        turn_id=1,
        payload={"finishReason": "completed"},
    )
    await _emit(session, finish)

    text = "".join(
        str(e.payload.get("content", "")) for e in sender.events if e.event == "agent.text.delta"
    )
    assert str(SANDBOX) not in text
    assert text == "保存在 ./output\\cat.png 完成"
    assert sender.events[-1].event == "agent.turn.finish"


@pytest.mark.asyncio
async def test_pending_partial_path_flushed_before_turn_finish() -> None:
    session = _FakeSession()
    sender = _FakeSender()
    attach_session(session, sender, set())

    # 只发路径的一半：应被压住不发出
    partial = str(SANDBOX)[:20]
    await _emit(session, _delta("前缀 " + partial))
    assert [e.event for e in sender.events] == ["agent.text.delta"]
    assert sender.events[0].payload["content"] == "前缀 "

    # 回合直接结束（路径没发完）：剩余缓冲必须在 finish 前补发
    finish = BridgeEvent(event="agent.turn.finish", session_id="demo_stream", turn_id=7, payload={})
    await _emit(session, finish)

    kinds = [e.event for e in sender.events]
    assert kinds == ["agent.text.delta", "agent.text.delta", "agent.turn.finish"]
    flushed = sender.events[1]
    assert flushed.turn_id == 7
    assert flushed.payload["content"] == partial


@pytest.mark.asyncio
async def test_reasoning_channel_is_sanitized_independently() -> None:
    session = _FakeSession()
    sender = _FakeSender()
    attach_session(session, sender, set())

    path = str(SANDBOX)
    await _emit(session, _delta(path[:10], event="agent.reasoning.delta"))
    await _emit(session, _delta(path[10:] + "\\x.png", event="agent.reasoning.delta"))
    error = BridgeEvent(
        event="agent.error", session_id="demo_stream", turn_id=1, payload={"content": "boom"}
    )
    await _emit(session, error)

    reasoning = "".join(
        str(e.payload.get("content", ""))
        for e in sender.events
        if e.event == "agent.reasoning.delta"
    )
    assert str(SANDBOX) not in reasoning
    assert reasoning == "./x.png"


@pytest.mark.asyncio
async def test_tool_result_payload_still_sanitized_statelessly() -> None:
    session = _FakeSession()
    sender = _FakeSender()
    attach_session(session, sender, set())

    await _emit(
        session,
        BridgeEvent(
            event="agent.tool.result",
            session_id="demo_stream",
            turn_id=1,
            payload={"toolName": "read", "toolResult": str(SANDBOX) + "\\a.py"},
        ),
    )
    assert sender.events[-1].payload["toolResult"] == "./a.py"


@pytest.mark.asyncio
async def test_reattach_on_same_connection_keeps_buffer() -> None:
    session = _FakeSession()
    sender = _FakeSender()
    attached: set = set()
    attach_session(session, sender, attached)

    path = str(SANDBOX)
    await _emit(session, _delta(path[:10]))
    # 每个请求都会重复 attach；不能重置缓冲
    attach_session(session, sender, attached)
    await _emit(session, _delta(path[10:] + "\\y.png"))
    finish = BridgeEvent(
        event="agent.turn.finish", session_id="demo_stream", turn_id=1, payload={}
    )
    await _emit(session, finish)

    text = "".join(
        str(e.payload.get("content", "")) for e in sender.events if e.event == "agent.text.delta"
    )
    assert str(SANDBOX) not in text
    assert text == "./y.png"
