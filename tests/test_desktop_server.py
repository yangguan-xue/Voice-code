from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from langchain_core.messages import HumanMessage
from websockets.asyncio.client import connect

from voice_code.agent.abort import AbortSignal
from voice_code.agent.types import AgentEvent, EventType
from voice_code.desktop.bridge import DesktopAgentBridge
from voice_code.desktop.permissions import DesktopPermissionApprover
from voice_code.desktop.server import DesktopBridgeServer
from voice_code.permissions import PermissionBehavior, PermissionRequest
from voice_code.session.manager import get_session_path
from voice_code.session.transcript import TranscriptWriter


class _FakeDesktopActions:
    def bootstrap_context(self) -> dict[str, object]:
        return {
            "workspacePath": "D:/workspace/project",
            "branch": "codex/mac-desktop-ui",
            "branches": ["codex/mac-desktop-ui", "main"],
            "activeProfile": "deepseek",
            "modelProfiles": [
                {"id": "deepseek", "label": "deepseek", "modelName": "deepseek-v4-pro"},
                {"id": "local_openai", "label": "local_openai", "modelName": "your-local-model"},
            ],
        }

    def resume_session(self, session_id: str) -> dict[str, object]:
        return {
            "sessionId": session_id,
            "conversation": {
                "activeTurnId": None,
                "turns": [
                    {
                        "id": 1,
                        "sessionId": session_id,
                        "userText": "继续之前的任务",
                        "assistantText": "已经恢复。",
                        "reasoningText": "",
                        "status": "completed",
                        "finishReason": "completed",
                        "tools": [],
                        "errorText": "",
                    }
                ],
            },
        }

    def create_session(self) -> dict[str, object]:
        return {"sessionId": "new-session", "workspacePath": "D:/workspace/project"}

    def checkout_branch(self, branch: str) -> dict[str, object]:
        return {"branch": branch, "branches": [branch, "main"]}

    async def select_model_profile(self, profile: str) -> dict[str, object]:
        model_name = "deepseek-v4-pro" if profile == "deepseek" else "your-local-model"
        return {"profile": profile, "modelName": model_name}

    async def configure_model(self, config: dict[str, str]) -> dict[str, object]:
        return {"profile": config["id"], "modelName": config["modelName"]}


async def _runner(
    _text: str,
    *,
    abort_signal: AbortSignal,
) -> AsyncIterator[AgentEvent]:
    assert not abort_signal.is_triggered()
    yield AgentEvent(type=EventType.TEXT, turn=1, content="桌面 bridge 已连接。")
    yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")


async def _failing_runner(
    _text: str,
    *,
    abort_signal: AbortSignal,
) -> AsyncIterator[AgentEvent]:
    assert not abort_signal.is_triggered()
    raise RuntimeError("model exploded")
    yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")


@pytest.mark.asyncio
async def test_desktop_websocket_server_streams_turn_events() -> None:
    bridge = DesktopAgentBridge(session_id="session-1", runner=_runner)
    server = DesktopBridgeServer(bridge=bridge, token="test-token")

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "req-1",
                        "type": "request",
                        "method": "turn.start",
                        "token": "test-token",
                        "payload": {"text": "检查桌面 bridge"},
                    }
                )
            )

            messages = [json.loads(await websocket.recv()) for _ in range(4)]

    response = next(message for message in messages if message["type"] == "response")
    events = [message for message in messages if message["type"] == "event"]

    assert response == {
        "id": "req-1",
        "type": "response",
        "ok": True,
        "payload": {"sessionId": "session-1", "turnId": 1, "finishReason": "completed"},
    }
    assert [message["event"] for message in events] == [
        "agent.turn.started",
        "agent.text.delta",
        "agent.turn.finish",
    ]
    assert events[1]["payload"]["content"] == "桌面 bridge 已连接。"


@pytest.mark.asyncio
async def test_desktop_websocket_server_rejects_bad_token() -> None:
    bridge = DesktopAgentBridge(session_id="session-1", runner=_runner)
    server = DesktopBridgeServer(bridge=bridge, token="test-token")

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "req-1",
                        "type": "request",
                        "method": "app.bootstrap",
                        "token": "wrong",
                        "payload": {},
                    }
                )
            )
            response = json.loads(await websocket.recv())

    assert response == {
        "id": "req-1",
        "type": "response",
        "ok": False,
        "error": {"code": "UNAUTHORIZED", "message": "Invalid desktop bridge token."},
    }


@pytest.mark.asyncio
async def test_desktop_websocket_server_bootstrap_returns_session_groups(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("voice_code.session.manager.get_transcript_dir", lambda: tmp_path)

    writer1 = TranscriptWriter(
        get_session_path("20260101-000000-abcd"),
        session_meta={"cwd": "/Users/example/workspace/voice-code"},
    )
    writer1.write_message(HumanMessage(content="冒泡算法文档"))
    writer1.close()

    writer2 = TranscriptWriter(
        get_session_path("20260101-000001-efgh"),
        session_meta={"cwd": "/Users/example/notes"},
    )
    writer2.write_message(HumanMessage(content="整理笔记"))
    writer2.close()

    bridge = DesktopAgentBridge(session_id="session-1", runner=_runner)
    server = DesktopBridgeServer(bridge=bridge, token="test-token")

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "req-1",
                        "type": "request",
                        "method": "app.bootstrap",
                        "token": "test-token",
                        "payload": {},
                    }
                )
            )
            response = json.loads(await websocket.recv())

    assert response["ok"] is True
    assert response["payload"]["sessionId"] == "session-1"
    assert sorted(
        [
        (group["folderName"], [session["title"] for session in group["sessions"]])
        for group in response["payload"]["sessionGroups"]
        ]
    ) == [("notes", ["整理笔记"]), ("voice-code", ["冒泡算法文档"])]


@pytest.mark.asyncio
async def test_desktop_server_exposes_runtime_configuration_and_actions() -> None:
    bridge = DesktopAgentBridge(session_id="session-1", runner=_runner)
    server = DesktopBridgeServer(
        bridge=bridge,
        token="test-token",
        actions=_FakeDesktopActions(),
    )

    bootstrap = await server._handle_raw_message(
        json.dumps(
            {
                "id": "bootstrap-1",
                "type": "request",
                "method": "app.bootstrap",
                "token": "test-token",
                "payload": {},
            }
        )
    )
    resumed = await server._handle_raw_message(
        json.dumps(
            {
                "id": "resume-1",
                "type": "request",
                "method": "session.resume",
                "token": "test-token",
                "payload": {"sessionId": "saved-session"},
            }
        )
    )
    created = await server._handle_raw_message(
        json.dumps(
            {
                "id": "create-1",
                "type": "request",
                "method": "session.create",
                "token": "test-token",
                "payload": {},
            }
        )
    )
    branch = await server._handle_raw_message(
        json.dumps(
            {
                "id": "branch-1",
                "type": "request",
                "method": "git.checkout",
                "token": "test-token",
                "payload": {"branch": "feature/test"},
            }
        )
    )
    model = await server._handle_raw_message(
        json.dumps(
            {
                "id": "model-1",
                "type": "request",
                "method": "model.select",
                "token": "test-token",
                "payload": {"profile": "deepseek"},
            }
        )
    )
    custom_model = await server._handle_raw_message(
        json.dumps(
            {
                "id": "model-2",
                "type": "request",
                "method": "model.configure",
                "token": "test-token",
                "payload": {
                    "id": "custom-company",
                    "displayName": "Company GPT",
                    "baseUrl": "https://llm.example.com/v1",
                    "apiKey": "secret",
                    "modelName": "gpt-company",
                },
            }
        )
    )

    assert bootstrap.ok is True
    assert bootstrap.payload["branch"] == "codex/mac-desktop-ui"
    assert bootstrap.payload["activeProfile"] == "deepseek"
    assert resumed.payload["sessionId"] == "saved-session"
    assert created.payload == {"sessionId": "new-session", "workspacePath": "D:/workspace/project"}
    assert resumed.payload["conversation"]["turns"][0]["assistantText"] == "已经恢复。"
    assert branch.payload == {"branch": "feature/test", "branches": ["feature/test", "main"]}
    assert model.payload == {"profile": "deepseek", "modelName": "deepseek-v4-pro"}
    assert custom_model.payload == {"profile": "custom-company", "modelName": "gpt-company"}


@pytest.mark.asyncio
async def test_desktop_websocket_server_reports_validation_errors() -> None:
    bridge = DesktopAgentBridge(session_id="session-1", runner=_runner)
    server = DesktopBridgeServer(bridge=bridge, token="test-token")

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send("not json")
            response = json.loads(await websocket.recv())

    assert response["type"] == "response"
    assert response["ok"] is False
    assert response["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_desktop_websocket_server_reports_turn_failures() -> None:
    bridge = DesktopAgentBridge(session_id="session-1", runner=_failing_runner)
    server = DesktopBridgeServer(bridge=bridge, token="test-token")

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "req-1",
                        "type": "request",
                        "method": "turn.start",
                        "token": "test-token",
                        "payload": {"text": "触发错误"},
                    }
                )
            )

            messages = [json.loads(await websocket.recv()) for _ in range(2)]

    response = next(message for message in messages if message["type"] == "response")
    events = [message for message in messages if message["type"] == "event"]

    assert [event["event"] for event in events] == ["agent.turn.started"]
    assert response == {
        "id": "req-1",
        "type": "response",
        "ok": False,
        "error": {"code": "TURN_FAILED", "message": "Agent turn failed."},
    }


@pytest.mark.asyncio
async def test_desktop_websocket_server_accepts_permission_resolution_during_active_turn() -> None:
    events: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    bridge_ref: dict[str, DesktopAgentBridge] = {}

    def emit_permission_event(event) -> None:
        bridge = bridge_ref["bridge"]
        asyncio.run_coroutine_threadsafe(bridge.emit_event(event), loop)

    approver = DesktopPermissionApprover(emit=emit_permission_event, session_id="session-1")

    async def runner(
        _text: str,
        *,
        abort_signal: AbortSignal,
    ) -> AsyncIterator[AgentEvent]:
        assert not abort_signal.is_triggered()
        request = PermissionRequest(
            tool_name="bash",
            tool_input={"command": "touch example.txt"},
            reason="Non-readonly tools require approval by default.",
            session_id="session-1",
        )
        decision = await asyncio.to_thread(approver.approve, request)
        assert decision.behavior == PermissionBehavior.ALLOW
        yield AgentEvent(type=EventType.TEXT, turn=1, content="权限已通过。")
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

    bridge = DesktopAgentBridge(session_id="session-1", runner=runner)
    bridge_ref["bridge"] = bridge
    server = DesktopBridgeServer(
        bridge=bridge,
        token="test-token",
        permission_approver=approver,
    )

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "turn-1",
                        "type": "request",
                        "method": "turn.start",
                        "token": "test-token",
                        "payload": {"text": "需要权限"},
                    }
                )
            )

            while True:
                message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))
                events.append(message)
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
                for message in events
            ):
                events.append(json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)))

    permission_response = next(
        message
        for message in events
        if message.get("type") == "response" and message["id"] == "perm-1"
    )
    turn_response = next(
        message
        for message in events
        if message.get("type") == "response" and message["id"] == "turn-1"
    )

    assert permission_response["ok"] is True
    assert permission_response["payload"] == {"resolved": True}
    assert turn_response["ok"] is True
    assert [message.get("event") for message in events if message.get("type") == "event"] == [
        "agent.turn.started",
        "permission.request",
        "agent.text.delta",
        "agent.turn.finish",
    ]
