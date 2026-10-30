"""Tests for the FastAPI transport of the web demo (tenant model).

钉住：访问码登录 → Bearer token → WS attach → 回合流 → 会话列表/切换/历史回放 →
租户隔离。旧游客模式（demo.session.create / X-Access-Code）已下线。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from starlette.websockets import WebSocketDisconnect

from voice_code.agent.abort import AbortSignal
from voice_code.agent.types import AgentEvent, EventType
from voice_code.permissions import PermissionBehavior, PermissionRequest
from voice_code.runtime import RuntimeBootstrap
from voice_code.session import TranscriptWriter
from voice_code.web_demo.app import create_app
from voice_code.web_demo.limits import WebDemoLimits
from voice_code.web_demo.permissions import WebPermissionApprover
from voice_code.web_demo.runtime import TenantSession, TenantSessionManager
from voice_code.web_demo.tenants import TenantStore


def _template(path: Path) -> Path:
    template = path / "template"
    template.mkdir(exist_ok=True)
    (template / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return template


def _text_runner_factory(message: str):
    def factory(_session: TenantSession, _approver: WebPermissionApprover):
        async def runner(
            _text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
            user_images: list[dict[str, str]] | None = None,
        ) -> AsyncIterator[AgentEvent]:
            assert not abort_signal.is_triggered()
            yield AgentEvent(type=EventType.TEXT, turn=1, content=message)
            yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

        return runner

    return factory


def _make_manager(
    tmp_path: Path, runner_message: str = "已完成。", **kwargs
) -> TenantSessionManager:
    store = TenantStore.from_access_codes_csv(
        tmp_path / "data" / "web-demo.db", "alice=Alice,bob=Bob"
    )
    return TenantSessionManager(
        tenant_store=store,
        sandbox_root=tmp_path / "sandboxes",
        data_root=tmp_path / "data",
        template_repo=_template(tmp_path),
        runner_factory=_text_runner_factory(runner_message),
        **kwargs,
    )


def _make_app(tmp_path: Path, runner_message: str = "已完成。", **kwargs):
    return create_app(session_manager=_make_manager(tmp_path, runner_message, **kwargs))


def _login(client: TestClient, code: str = "alice") -> str:
    response = client.post("/api/auth/login", json={"accessCode": code})
    assert response.status_code == 201
    return str(response.json()["tenantToken"])


def _ws_attach(ws, token: str) -> dict:
    ws.send_text(
        json.dumps(
            {
                "id": "attach-1",
                "type": "request",
                "method": "tenant.session.attach",
                "sessionToken": token,
                "payload": {},
            }
        )
    )
    response = json.loads(ws.receive_text())
    assert response["ok"] is True
    return response


def _collect_until_response(ws, response_id: str) -> list[dict]:
    messages: list[dict] = []
    while True:
        message = json.loads(ws.receive_text())
        messages.append(message)
        if message.get("type") == "response" and message.get("id") == response_id:
            return messages


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- health / 登录 / token ---


def test_app_health(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_and_me(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        token = _login(client)
        me = client.get("/api/auth/me", headers=_bearer(token))
        assert me.status_code == 200
        assert me.json() == {"tenantId": "alice", "tenantLabel": "Alice"}


def test_login_wrong_code_rejected(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        response = client.post("/api/auth/login", json={"accessCode": "wrong"})
    assert response.status_code == 401


def test_me_requires_bearer(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        missing = client.get("/api/auth/me")
        assert missing.status_code == 422
        bad = client.get("/api/auth/me", headers=_bearer("not-a-token"))
        assert bad.status_code == 401


def test_logout_revokes_token(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        token = _login(client)
        out = client.post("/api/auth/logout", headers=_bearer(token))
        assert out.status_code == 204
        me = client.get("/api/auth/me", headers=_bearer(token))
        assert me.status_code == 401


def test_login_rate_limited(tmp_path: Path) -> None:
    manager = _make_manager(
        tmp_path, limits=WebDemoLimits(max_session_creates_per_minute=2)
    )
    with TestClient(create_app(session_manager=manager)) as client:
        first = client.post("/api/auth/login", json={"accessCode": "alice"})
        second = client.post("/api/auth/login", json={"accessCode": "alice"})
        third = client.post("/api/auth/login", json={"accessCode": "alice"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert third.status_code == 429

# --- WS attach / 回合流 ---


def test_websocket_attach_with_bad_token(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        with client.websocket_connect("/") as ws:
            ws.send_text(
                json.dumps(
                    {
                        "id": "attach-bad",
                        "type": "request",
                        "method": "tenant.session.attach",
                        "sessionToken": "not-a-token",
                        "payload": {},
                    }
                )
            )
            response = json.loads(ws.receive_text())
    assert response["ok"] is False
    assert response["error"]["code"] == "UNAUTHORIZED"


def test_websocket_full_turn_flow(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        token = _login(client)
        with client.websocket_connect("/") as ws:
            attach = _ws_attach(ws, token)
            payload = attach["payload"]
            assert payload["tenantId"] == "alice"
            assert payload["conversationId"]
            assert payload["workspaceLabel"]

            ws.send_text(
                json.dumps(
                    {
                        "id": "turn-1",
                        "type": "request",
                        "method": "turn.start",
                        "sessionToken": token,
                        "payload": {"text": "改 app.py", "permissionMode": "default"},
                    }
                )
            )
            messages = _collect_until_response(ws, "turn-1")

    response = next(message for message in messages if message.get("id") == "turn-1")
    events = [message for message in messages if message.get("type") == "event"]
    event_names = [event["event"] for event in events]

    assert response["ok"] is True
    assert "agent.turn.started" in event_names
    assert "agent.text.delta" in event_names
    assert "agent.turn.finish" in event_names
    assert "sandbox.diff.updated" in event_names
    # 首条用户消息落标题后会推送 session.updated 让侧边栏刷新
    assert "session.updated" in event_names
    updated = next(e for e in events if e["event"] == "session.updated")
    assert updated["payload"]["title"] == "改 app.py"


def test_websocket_rejects_bad_origin(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/", headers={"origin": "http://evil.example"}):
                pass


# --- 会话列表 / 切换 / 历史回放 ---


def test_conversations_list_new_and_activate(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        token = _login(client)
        with client.websocket_connect("/") as ws:
            first = _ws_attach(ws, token)["payload"]["conversationId"]

        listing = client.get("/api/conversations", headers=_bearer(token))
        assert listing.status_code == 200
        body = listing.json()
        assert [c["conversationId"] for c in body["conversations"]] == [first]
        assert body["conversations"][0]["isCurrent"] is True

        created = client.post("/api/conversations/new", headers=_bearer(token))
        assert created.status_code == 201
        second = created.json()["conversationId"]
        assert second != first

        listing = client.get("/api/conversations", headers=_bearer(token)).json()
        assert len(listing["conversations"]) == 2
        assert listing["currentConversationId"] == second

        activated = client.post(
            f"/api/conversations/{first}/activate", headers=_bearer(token)
        )
        assert activated.status_code == 200
        assert activated.json()["conversationId"] == first
        assert client.get("/api/conversations", headers=_bearer(token)).json()[
            "currentConversationId"
        ] == first


def test_history_messages_replay(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        token = _login(client)
        with client.websocket_connect("/") as ws:
            conversation_id = _ws_attach(ws, token)["payload"]["conversationId"]

        # 直接写 transcript，模拟一段历史
        manager: TenantSessionManager = client.app.state.session_manager
        record = manager.get_conversation_record(token, conversation_id)
        transcript_path = manager.resolve_transcript_path("alice", record)
        writer = TranscriptWriter(transcript_path)
        writer.write_message(HumanMessage(content="帮我改 app.py"))
        writer.write_message(
            AIMessage(
                content="好的，先读一下。",
                tool_calls=[{"name": "read", "args": {"file_path": "app.py"}, "id": "call-1"}],
            )
        )
        writer.write_message(
            ToolMessage(content="VALUE = 1", tool_call_id="call-1", name="read")
        )
        writer.write_message(AIMessage(content="已更新完成。"))
        writer.close()

        response = client.get(
            f"/api/conversations/{conversation_id}/messages", headers=_bearer(token)
        )
        assert response.status_code == 200
        turns = response.json()["turns"]
        assert len(turns) == 1
        turn = turns[0]
        assert turn["userText"] == "帮我改 app.py"
        assert "好的，先读一下。" in turn["assistantText"]
        assert "已更新完成。" in turn["assistantText"]
        assert turn["status"] == "completed"
        assert len(turn["tools"]) == 1
        tool = turn["tools"][0]
        assert tool["name"] == "read"
        assert tool["status"] == "completed"
        assert "VALUE = 1" in tool["result"]


def test_cross_tenant_conversation_returns_404(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        alice_token = _login(client, "alice")
        bob_token = _login(client, "bob")
        with client.websocket_connect("/") as ws:
            alice_conversation = _ws_attach(ws, alice_token)["payload"]["conversationId"]

        # bob 查 alice 的会话：404（不泄露存在性）
        response = client.get(
            f"/api/conversations/{alice_conversation}/messages", headers=_bearer(bob_token)
        )
        assert response.status_code == 404
        activated = client.post(
            f"/api/conversations/{alice_conversation}/activate", headers=_bearer(bob_token)
        )
        assert activated.status_code == 404


def test_upload_file_to_current_sandbox(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        token = _login(client)
        with client.websocket_connect("/") as ws:
            _ws_attach(ws, token)
        response = client.post(
            "/api/conversations/current/files?path=notes/hello.txt",
            headers=_bearer(token),
            content=b"hello tenant",
        )
        assert response.status_code == 201
        assert response.json()["path"] == "notes/hello.txt"

        manager: TenantSessionManager = client.app.state.session_manager
        written = manager.tenant_workspace_path("alice") / "notes" / "hello.txt"
        assert written.read_text(encoding="utf-8") == "hello tenant"


def test_upload_requires_bearer(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        response = client.post(
            "/api/conversations/current/files?path=a.txt", content=b"x"
        )
    assert response.status_code == 422

# --- 从旧 legacy 传输测试移植的安全/行为钉子 ---


def test_permission_resolution_during_turn(tmp_path: Path) -> None:
    def runner_factory(session: TenantSession, approver: WebPermissionApprover):
        async def runner(
            _text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
            user_images: list[dict[str, str]] | None = None,
        ) -> AsyncIterator[AgentEvent]:
            assert not abort_signal.is_triggered()
            request = PermissionRequest(
                tool_name="write",
                tool_input={"file_path": str(session.sandbox_path / "app.py")},
                reason="Non-readonly tools require approval by default.",
                session_id=session.conversation_id,
            )
            decision = await asyncio.to_thread(approver.approve, request)
            assert decision.behavior == PermissionBehavior.ALLOW
            yield AgentEvent(type=EventType.TEXT, turn=1, content="权限已通过。")
            yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

        return runner

    manager = _make_manager(tmp_path)
    manager._runner_factory = runner_factory
    with TestClient(create_app(session_manager=manager)) as client:
        token = _login(client)
        with client.websocket_connect("/") as ws:
            _ws_attach(ws, token)
            ws.send_text(
                json.dumps(
                    {
                        "id": "turn-1",
                        "type": "request",
                        "method": "turn.start",
                        "sessionToken": token,
                        "payload": {"text": "需要写文件"},
                    }
                )
            )
            events: list[dict] = []
            while True:
                message = json.loads(ws.receive_text())
                events.append(message)
                if message.get("event") == "permission.request":
                    ws.send_text(
                        json.dumps(
                            {
                                "id": "resolve-1",
                                "type": "request",
                                "method": "permission.resolve",
                                "sessionToken": token,
                                "payload": {
                                    "requestId": message["payload"]["requestId"],
                                    "behavior": "allow",
                                },
                            }
                        )
                    )
                if message.get("type") == "response" and message.get("id") == "turn-1":
                    break

    event_names = [e.get("event") for e in events if e.get("type") == "event"]
    assert "permission.request" in event_names
    response = next(e for e in events if e.get("id") == "turn-1")
    assert response["ok"] is True


def test_streamed_tool_paths_sanitized(tmp_path: Path) -> None:
    def runner_factory(session: TenantSession, _approver: WebPermissionApprover):
        async def runner(
            _text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
            user_images: list[dict[str, str]] | None = None,
        ) -> AsyncIterator[AgentEvent]:
            assert not abort_signal.is_triggered()
            tool_path = session.sandbox_path / "snake_game.py"
            yield AgentEvent(
                type=EventType.TOOL_RESULT,
                turn=1,
                tool_call_id="write-1",
                tool_name="write",
                tool_result=f"The file {tool_path} has been updated.",
            )
            yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

        return runner

    manager = _make_manager(tmp_path)
    manager._runner_factory = runner_factory
    with TestClient(create_app(session_manager=manager)) as client:
        token = _login(client)
        with client.websocket_connect("/") as ws:
            _ws_attach(ws, token)
            ws.send_text(
                json.dumps(
                    {
                        "id": "turn-1",
                        "type": "request",
                        "method": "turn.start",
                        "sessionToken": token,
                        "payload": {"text": "写个文件"},
                    }
                )
            )
            messages = _collect_until_response(ws, "turn-1")

    tool_event = next(m for m in messages if m.get("event") == "agent.tool.result")
    assert "./snake_game.py" in tool_event["payload"]["toolResult"]
    assert str(tmp_path) not in json.dumps(tool_event)


def test_sandbox_file_get_and_download_limit(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    with TestClient(create_app(session_manager=manager)) as client:
        token = _login(client)
        with client.websocket_connect("/") as ws:
            _ws_attach(ws, token)
            workspace = manager.tenant_workspace_path("alice")
            (workspace / "notes.py").write_text("print('hi')\n", encoding="utf-8")

            ws.send_text(
                json.dumps(
                    {
                        "id": "file-1",
                        "type": "request",
                        "method": "sandbox.file.get",
                        "sessionToken": token,
                        "payload": {"path": "notes.py"},
                    }
                )
            )
            response = json.loads(ws.receive_text())

    assert response["ok"] is True
    assert response["payload"]["content"] == "print('hi')\n"


def test_large_file_download_rejected(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path, limits=WebDemoLimits(max_download_bytes=8))
    with TestClient(create_app(session_manager=manager)) as client:
        token = _login(client)
        with client.websocket_connect("/") as ws:
            _ws_attach(ws, token)
            workspace = manager.tenant_workspace_path("alice")
            (workspace / "notes.py").write_text("print('hello world')\n", encoding="utf-8")
            ws.send_text(
                json.dumps(
                    {
                        "id": "file-1",
                        "type": "request",
                        "method": "sandbox.file.get",
                        "sessionToken": token,
                        "payload": {"path": "notes.py"},
                    }
                )
            )
            response = json.loads(ws.receive_text())

    assert response["ok"] is False
    assert response["error"]["code"] == "FILE_TOO_LARGE"

# --- 上下文续接（移植自旧 _create_default_runner 测试，适配懒加载结构） ---


@pytest.mark.asyncio
async def test_runner_preserves_context_between_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一热 runtime 内，第二回合的 resume_messages 含第一回合消息（含刷新的 system）。"""
    store = TenantStore.from_access_codes_csv(
        tmp_path / "data" / "web-demo.db", "alice=Alice"
    )
    manager = TenantSessionManager(
        tenant_store=store,
        sandbox_root=tmp_path / "sandboxes",
        data_root=tmp_path / "data",
        template_repo=_template(tmp_path),
        runner_factory=None,  # 走懒加载默认路径
        limits=WebDemoLimits(turn_timeout_seconds=5),
    )
    _tenant, token, _expires = manager.login("alice")
    session = await manager.attach(token)

    observed_resume: list[list[str] | None] = []

    async def fake_bootstrap_demo_runtime(
        *,
        workspace: str,
        profile: str | None,
        model_name: str | None,
        limits,
        session_id: str | None = None,
        resume_messages=None,
        transcript_dir=None,
    ) -> RuntimeBootstrap:
        assert workspace == str(session.sandbox_path)
        assert session_id == session.conversation_id
        assert transcript_dir == session.transcript_path.parent
        transcript = TranscriptWriter(
            session.transcript_path,
            session_meta={"cwd": workspace},
        )
        return RuntimeBootstrap(
            model=SimpleNamespace(model_name="demo-model"),
            fallback_model=None,
            fallback_profile=None,
            tools=[],
            cwd=workspace,
            prompt="system prompt",
            session_id=session_id,
            transcript_writer=transcript,
            resume_messages=resume_messages,
            memory_service=None,
            memory_project_key="demo",
        )

    async def passthrough_worker(
        base_runner,
        text: str,
        *,
        abort_signal: AbortSignal,
        permission_mode: str | None = None,
        **kwargs,
    ):
        async for event in base_runner(
            text,
            abort_signal=abort_signal,
            permission_mode=permission_mode,
        ):
            yield event

    async def fake_agent_loop(
        user_input: str,
        tools,
        system_prompt: str,
        model,
        *,
        resume_messages,
        transcript_writer,
        **kwargs,
    ):
        del tools, system_prompt, model, kwargs
        if resume_messages is None:
            observed_resume.append(None)
        else:
            observed_resume.append([type(m).__name__ for m in resume_messages])
        transcript_writer.write_message(HumanMessage(content=user_input))
        transcript_writer.write_message(AIMessage(content=f"answer:{user_input}"))
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

    import voice_code.web_demo.runtime as runtime_module

    monkeypatch.setattr(
        runtime_module, "_bootstrap_demo_runtime", fake_bootstrap_demo_runtime
    )
    monkeypatch.setattr(runtime_module, "run_turn_in_worker_thread", passthrough_worker)
    monkeypatch.setattr(runtime_module, "agent_loop", fake_agent_loop)

    assert session.bridge is not None
    await session.bridge.start_turn("问题一", permission_mode="default")
    await session.bridge.start_turn("问题二", permission_mode="default")
    await manager.shutdown()

    assert observed_resume[0] is None  # 第一回合无历史
    assert observed_resume[1] == ["SystemMessage", "HumanMessage", "AIMessage"]
