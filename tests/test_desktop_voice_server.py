from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from websockets.asyncio.client import connect

from voice_code.desktop.permissions import DesktopPermissionApprover
from voice_code.desktop_voice.bridge import DesktopVoiceBridge
from voice_code.desktop_voice.server import DesktopVoiceServer
from voice_code.permissions import PermissionBehavior, PermissionRequest


class _FakeAgentBridge:
    def __init__(self) -> None:
        self.context_calls: list[list[object] | None] = []

    def on_event(self, callback) -> None:
        self._callback = callback

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def run_turn(self, text: str, **kwargs) -> str:
        self.context_calls.append(kwargs.get("context_messages"))
        return f"回复：{text}"

    async def summarize_for_speech(self, text: str) -> str:
        return text

    def interrupt(self) -> None:
        return None


@pytest.mark.asyncio
async def test_desktop_voice_server_streams_clean_turn_events() -> None:
    bridge = DesktopVoiceBridge(
        session_id="voice-session",
        agent_bridge=_FakeAgentBridge(),  # type: ignore[arg-type]
    )
    server = DesktopVoiceServer(bridge=bridge, token="test-token")

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "turn-1",
                        "type": "request",
                        "method": "voice.turn.start",
                        "token": "test-token",
                        "payload": {"text": "你好"},
                    }
                )
            )
            messages = [json.loads(await websocket.recv()) for _ in range(5)]

    response = next(message for message in messages if message["type"] == "response")
    events = [message for message in messages if message["type"] == "event"]

    assert response == {
        "id": "turn-1",
        "type": "response",
        "ok": True,
        "payload": {
            "sessionId": "voice-session",
            "turnId": 1,
            "finishReason": "completed",
            "mode": "independentSession",
            "contextVersion": 0,
            "worktreeTaskId": "",
            "worktreePath": "",
        },
    }
    assert [event["event"] for event in events] == [
        "voice.user.final",
        "voice.state",
        "voice.assistant.final",
        "voice.state",
    ]


@pytest.mark.asyncio
async def test_desktop_voice_server_bootstrap_and_tts_mute() -> None:
    bridge = DesktopVoiceBridge(
        session_id="voice-session",
        agent_bridge=_FakeAgentBridge(),  # type: ignore[arg-type]
    )
    server = DesktopVoiceServer(bridge=bridge, token="test-token")

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "mute-1",
                        "type": "request",
                        "method": "voice.tts.setMuted",
                        "token": "test-token",
                        "payload": {"muted": True},
                    }
                )
            )
            mute_response = json.loads(await websocket.recv())

            await websocket.send(
                json.dumps(
                    {
                        "id": "boot-1",
                        "type": "request",
                        "method": "voice.bootstrap",
                        "token": "test-token",
                        "payload": {},
                    }
                )
            )
            boot_response = json.loads(await websocket.recv())

    assert mute_response["ok"] is True
    assert mute_response["payload"] == {"ttsMuted": True}
    assert boot_response["ok"] is True
    assert boot_response["payload"] == {"sessionId": "voice-session", "ttsMuted": True}


@pytest.mark.asyncio
async def test_desktop_voice_server_accepts_shared_context_messages() -> None:
    agent = _FakeAgentBridge()
    bridge = DesktopVoiceBridge(
        session_id="voice-session",
        agent_bridge=agent,  # type: ignore[arg-type]
    )
    server = DesktopVoiceServer(bridge=bridge, token="test-token")

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "turn-1",
                        "type": "request",
                        "method": "voice.turn.start",
                        "token": "test-token",
                        "payload": {
                            "text": "接着说",
                            "mode": "sharedSession",
                            "contextVersion": 4,
                            "contextMessages": [
                                {"role": "user", "content": "主会话问题"},
                                {"role": "assistant", "content": "主会话回答"},
                                {"role": "tool", "content": "不能进上下文"},
                            ],
                        },
                    }
                )
            )
            for _ in range(5):
                await websocket.recv()

    assert agent.context_calls
    context = agent.context_calls[0]
    assert isinstance(context[0], HumanMessage)  # type: ignore[index]
    assert isinstance(context[1], AIMessage)  # type: ignore[index]
    assert [message.content for message in context] == [  # type: ignore[union-attr]
        "主会话问题",
        "主会话回答",
    ]


@pytest.mark.asyncio
async def test_desktop_voice_server_resolves_permission_during_active_turn() -> None:
    loop = asyncio.get_running_loop()
    bridge_ref: dict[str, DesktopVoiceBridge] = {}

    def emit_permission_event(event) -> None:
        bridge = bridge_ref["bridge"]
        asyncio.run_coroutine_threadsafe(bridge.emit_event(event), loop)

    approver = DesktopPermissionApprover(
        emit=emit_permission_event,
        session_id="voice-session",
    )

    class PermissionAgentBridge(_FakeAgentBridge):
        async def run_turn(self, text: str) -> str:
            request = PermissionRequest(
                tool_name="bash",
                tool_input={"command": "touch voice.txt"},
                reason="Non-readonly tools require approval by default.",
                session_id="voice-session",
            )
            decision = await asyncio.to_thread(approver.approve, request)
            assert decision.behavior == PermissionBehavior.ALLOW
            return "权限已通过。"

    bridge = DesktopVoiceBridge(
        session_id="voice-session",
        agent_bridge=PermissionAgentBridge(),  # type: ignore[arg-type]
    )
    bridge_ref["bridge"] = bridge
    server = DesktopVoiceServer(
        bridge=bridge,
        token="test-token",
        permission_approver=approver,
    )
    messages: list[dict[str, object]] = []

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "turn-1",
                        "type": "request",
                        "method": "voice.turn.start",
                        "token": "test-token",
                        "payload": {"text": "需要权限"},
                    }
                )
            )

            while True:
                message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))
                messages.append(message)
                if message.get("event") == "permission.request":
                    request_id = message["payload"]["requestId"]
                    break

            await websocket.send(
                json.dumps(
                    {
                        "id": "perm-1",
                        "type": "request",
                        "method": "permission.resolve",
                        "token": "test-token",
                        "payload": {
                            "requestId": request_id,
                            "behavior": "allow",
                            "rememberScope": "",
                        },
                    }
                )
            )

            while not any(
                message.get("type") == "response" and message.get("id") == "turn-1"
                for message in messages
            ):
                messages.append(json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)))

    permission_response = next(
        message
        for message in messages
        if message.get("type") == "response" and message.get("id") == "perm-1"
    )
    turn_response = next(
        message
        for message in messages
        if message.get("type") == "response" and message.get("id") == "turn-1"
    )

    assert permission_response["payload"] == {"resolved": True}
    assert turn_response["ok"] is True
    assert [message.get("event") for message in messages if message.get("type") == "event"] == [
        "voice.user.final",
        "voice.state",
        "permission.request",
        "voice.assistant.final",
        "voice.state",
    ]
