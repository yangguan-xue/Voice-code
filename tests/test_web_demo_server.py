from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from websockets.asyncio.client import connect

from voice_code.agent.abort import AbortSignal
from voice_code.agent.types import AgentEvent, EventType
from voice_code.permissions import PermissionBehavior, PermissionRequest
from voice_code.runtime import RuntimeBootstrap
from voice_code.session.transcript import TranscriptWriter
from voice_code.web_demo.limits import WebDemoLimits
from voice_code.web_demo.permissions import WebPermissionApprover
from voice_code.web_demo.runtime import (
    DemoSession,
    DemoSessionManager,
    _build_web_demo_tools,
    _create_default_runner,
    _refresh_resumed_system_prompt,
)
from voice_code.web_demo.server import WebDemoServer


def _template(path: Path) -> Path:
    template = path / "template"
    template.mkdir()
    (template / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return template


def test_web_demo_runtime_disables_bash_tool_without_linux_sandbox(monkeypatch) -> None:
    tools = [
        SimpleNamespace(name="bash"),
        SimpleNamespace(name="read"),
        SimpleNamespace(name="glob"),
    ]
    monkeypatch.setattr(
        "voice_code.web_demo.runtime.create_web_demo_bash_tool",
        lambda workspace, limits=None: None,
    )

    filtered = _build_web_demo_tools(tools, workspace="/tmp/demo", limits=WebDemoLimits())

    assert [tool.name for tool in filtered] == ["read", "glob"]


def test_web_demo_runtime_enables_sandboxed_bash_when_available(monkeypatch) -> None:
    tools = [
        SimpleNamespace(name="bash"),
        SimpleNamespace(name="read"),
    ]
    monkeypatch.setattr(
        "voice_code.web_demo.runtime.create_web_demo_bash_tool",
        lambda workspace, limits=None: SimpleNamespace(
            name="bash",
            workspace=workspace,
            limits=limits,
        ),
    )

    filtered = _build_web_demo_tools(tools, workspace="/tmp/demo", limits=WebDemoLimits())

    assert [tool.name for tool in filtered] == ["bash", "read"]
    assert filtered[0].limits == WebDemoLimits()


@pytest.mark.asyncio
async def test_web_demo_runner_preserves_context_between_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = TranscriptWriter(
        tmp_path / "demo-session.jsonl",
        session_meta={"cwd": str(tmp_path)},
    )
    runtime = RuntimeBootstrap(
        model=SimpleNamespace(model_name="demo-model"),
        fallback_model=None,
        fallback_profile=None,
        tools=[],
        cwd=str(tmp_path),
        prompt="system prompt",
        session_id="demo-session",
        transcript_writer=transcript,
        resume_messages=None,
        memory_service=None,
        memory_project_key="demo",
    )
    session = DemoSession(
        session_id="demo-session",
        session_token="token",
        sandbox=SimpleNamespace(path=tmp_path, workspace_label="demo-repo"),
        expires_at=None,  # type: ignore[arg-type]
        loop=asyncio.get_running_loop(),
    )
    observed_resume_messages: list[list[str] | None] = []

    async def fake_bootstrap_demo_runtime(
        *,
        workspace: str,
        profile: str | None,
        model_name: str | None,
        limits,
    ):
        assert workspace == str(tmp_path)
        assert profile is None
        assert model_name is None
        assert limits.turn_timeout_seconds == 5.0
        return runtime

    async def passthrough_worker(
        base_runner,
        text: str,
        *,
        abort_signal: AbortSignal,
        permission_mode: str | None = None,
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
            observed_resume_messages.append(None)
        else:
            observed_resume_messages.append(
                [type(message).__name__ for message in resume_messages]
            )
        transcript_writer.write_message(HumanMessage(content=user_input))
        transcript_writer.write_message(AIMessage(content=f"answer:{user_input}"))
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

    monkeypatch.setattr(
        "voice_code.web_demo.runtime._bootstrap_demo_runtime",
        fake_bootstrap_demo_runtime,
    )
    monkeypatch.setattr(
        "voice_code.web_demo.runtime.run_turn_in_worker_thread",
        passthrough_worker,
    )
    monkeypatch.setattr(
        "voice_code.web_demo.runtime.agent_loop",
        fake_agent_loop,
    )

    runner = await _create_default_runner(
        session,
        WebPermissionApprover(
            emit=lambda event: None,
            sandbox=session.sandbox,  # type: ignore[arg-type]
            session_id=session.session_id,
            timeout_seconds=1.0,
        ),
        profile=None,
        model_name=None,
        limits=SimpleNamespace(turn_timeout_seconds=5.0),
    )

    events = []
    async for event in runner("first turn", abort_signal=AbortSignal()):
        events.append(event)
    assert events[-1].finish_reason == "completed"

    events = []
    async for event in runner("second turn", abort_signal=AbortSignal()):
        events.append(event)
    assert events[-1].finish_reason == "completed"

    assert observed_resume_messages[0] is None
    assert observed_resume_messages[1] == ["SystemMessage", "HumanMessage", "AIMessage"]
    assert [type(message).__name__ for message in runtime.resume_messages or []] == [
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
        "AIMessage",
    ]


def test_web_demo_refreshes_resumed_system_prompt() -> None:
    messages = [HumanMessage(content="hi"), AIMessage(content="ok")]

    refreshed = _refresh_resumed_system_prompt(messages, "new prompt")

    assert isinstance(refreshed, list)
    assert isinstance(refreshed[0], SystemMessage)
    assert refreshed[0].content == "new prompt"


@pytest.mark.asyncio
async def test_web_demo_rejects_invalid_access_code(tmp_path: Path) -> None:
    manager = DemoSessionManager(
        access_code="dev-demo",
        sandbox_root=tmp_path / "sandboxes",
        template_repo=_template(tmp_path),
        runner_factory=_text_runner_factory("unused"),
    )
    server = WebDemoServer(session_manager=manager)

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "create-1",
                        "type": "request",
                        "method": "demo.session.create",
                        "payload": {"accessCode": "wrong"},
                    }
                )
            )
            response = json.loads(await websocket.recv())

    assert response == {
        "id": "create-1",
        "type": "response",
        "ok": False,
        "error": {"code": "UNAUTHORIZED", "message": "Invalid demo access code."},
    }


@pytest.mark.asyncio
async def test_web_demo_streams_turn_and_diff(tmp_path: Path) -> None:
    def runner_factory(session: DemoSession, _approver: WebPermissionApprover):
        async def runner(
            _text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
        ) -> AsyncIterator[AgentEvent]:
            assert not abort_signal.is_triggered()
            assert permission_mode == "default"
            (session.sandbox_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
            yield AgentEvent(type=EventType.TEXT, turn=1, content="已更新健康检查示例。")
            yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

        return runner

    manager = DemoSessionManager(
        access_code="dev-demo",
        sandbox_root=tmp_path / "sandboxes",
        template_repo=_template(tmp_path),
        runner_factory=runner_factory,
    )
    server = WebDemoServer(session_manager=manager)

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            create_response = await _create_session(websocket)
            token = create_response["payload"]["sessionToken"]

            await websocket.send(
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

            messages = await _collect_until_response(websocket, "turn-1")

    response = next(message for message in messages if message.get("id") == "turn-1")
    events = [message for message in messages if message.get("type") == "event"]

    assert response["ok"] is True
    assert [event["event"] for event in events] == [
        "agent.turn.started",
        "agent.text.delta",
        "agent.turn.finish",
        "sandbox.diff.updated",
    ]
    diff_payload = events[-1]["payload"]
    assert diff_payload["changedFiles"] == [
        {"path": "app.py", "status": "modified", "additions": 1, "deletions": 1}
    ]
    assert str(tmp_path) not in json.dumps(diff_payload)


@pytest.mark.asyncio
async def test_web_demo_rejects_invalid_session_token(tmp_path: Path) -> None:
    manager = DemoSessionManager(
        access_code="dev-demo",
        sandbox_root=tmp_path / "sandboxes",
        template_repo=_template(tmp_path),
        runner_factory=_text_runner_factory("unused"),
    )
    server = WebDemoServer(session_manager=manager)

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": "turn-1",
                        "type": "request",
                        "method": "turn.start",
                        "sessionToken": "bad-token",
                        "payload": {"text": "hi"},
                    }
                )
            )
            response = json.loads(await websocket.recv())

    assert response["ok"] is False
    assert response["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_web_demo_accepts_permission_resolution_during_turn(tmp_path: Path) -> None:
    def runner_factory(session: DemoSession, approver: WebPermissionApprover):
        async def runner(
            _text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
        ) -> AsyncIterator[AgentEvent]:
            assert not abort_signal.is_triggered()
            request = PermissionRequest(
                tool_name="write",
                tool_input={"file_path": str(session.sandbox_path / "app.py")},
                reason="Non-readonly tools require approval by default.",
                session_id=session.session_id,
            )
            decision = await asyncio.to_thread(approver.approve, request)
            assert decision.behavior == PermissionBehavior.ALLOW
            yield AgentEvent(type=EventType.TEXT, turn=1, content="权限已通过。")
            yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

        return runner

    manager = DemoSessionManager(
        access_code="dev-demo",
        sandbox_root=tmp_path / "sandboxes",
        template_repo=_template(tmp_path),
        runner_factory=runner_factory,
    )
    server = WebDemoServer(session_manager=manager)

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            create_response = await _create_session(websocket)
            token = create_response["payload"]["sessionToken"]
            await websocket.send(
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

            events: list[dict[str, object]] = []
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
                        "sessionToken": token,
                        "payload": {
                            "requestId": request_id,
                            "behavior": "allow",
                            "rememberScope": "",
                        },
                    }
                )
            )
            events.extend(await _collect_until_response(websocket, "turn-1"))

    permission_event = next(event for event in events if event.get("event") == "permission.request")
    permission_response = next(event for event in events if event.get("id") == "perm-1")

    assert permission_event["payload"]["filePreview"] == "app.py"
    assert str(tmp_path) not in json.dumps(permission_event)
    assert permission_response["ok"] is True
    assert permission_response["payload"] == {"resolved": True}


@pytest.mark.asyncio
async def test_web_demo_sanitizes_tool_paths_in_streamed_events(tmp_path: Path) -> None:
    def runner_factory(session: DemoSession, _approver: WebPermissionApprover):
        async def runner(
            _text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
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

    manager = DemoSessionManager(
        access_code="dev-demo",
        sandbox_root=tmp_path / "sandboxes",
        template_repo=_template(tmp_path),
        runner_factory=runner_factory,
    )
    server = WebDemoServer(session_manager=manager)

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            create_response = await _create_session(websocket)
            token = create_response["payload"]["sessionToken"]
            await websocket.send(
                json.dumps(
                    {
                        "id": "turn-1",
                        "type": "request",
                        "method": "turn.start",
                        "sessionToken": token,
                        "payload": {"text": "更新文件"},
                    }
                )
            )
            messages = await _collect_until_response(websocket, "turn-1")

    tool_event = next(
        message for message in messages if message.get("event") == "agent.tool.result"
    )
    payload = tool_event["payload"]

    assert "./snake_game.py" in payload["toolResult"]
    assert str(tmp_path) not in json.dumps(tool_event)


@pytest.mark.asyncio
async def test_web_demo_strips_runtime_noise_from_glob_results(tmp_path: Path) -> None:
    def runner_factory(_session: DemoSession, _approver: WebPermissionApprover):
        async def runner(
            _text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
        ) -> AsyncIterator[AgentEvent]:
            assert not abort_signal.is_triggered()
            yield AgentEvent(
                type=EventType.TOOL_RESULT,
                turn=1,
                tool_call_id="glob-1",
                tool_name="glob",
                tool_result=".git/\n.reasoning/\nREADME.md\n__pycache__/\napp.py",
            )
            yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

        return runner

    manager = DemoSessionManager(
        access_code="dev-demo",
        sandbox_root=tmp_path / "sandboxes",
        template_repo=_template(tmp_path),
        runner_factory=runner_factory,
    )
    server = WebDemoServer(session_manager=manager)

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            create_response = await _create_session(websocket)
            token = create_response["payload"]["sessionToken"]
            await websocket.send(
                json.dumps(
                    {
                        "id": "turn-1",
                        "type": "request",
                        "method": "turn.start",
                        "sessionToken": token,
                        "payload": {"text": "列出文件"},
                    }
                )
            )
            messages = await _collect_until_response(websocket, "turn-1")

    tool_event = next(
        message for message in messages if message.get("event") == "agent.tool.result"
    )
    payload = tool_event["payload"]

    assert payload["toolResult"] == "README.md\napp.py"


@pytest.mark.asyncio
async def test_web_demo_can_return_sandbox_file_contents(tmp_path: Path) -> None:
    manager = DemoSessionManager(
        access_code="dev-demo",
        sandbox_root=tmp_path / "sandboxes",
        template_repo=_template(tmp_path),
        runner_factory=_text_runner_factory("unused"),
    )
    server = WebDemoServer(session_manager=manager)

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            create_response = await _create_session(websocket)
            token = create_response["payload"]["sessionToken"]
            session_id = create_response["payload"]["sessionId"]
            sandbox_file = tmp_path / "sandboxes" / session_id / "notes.py"
            sandbox_file.write_text("print('hi')\n", encoding="utf-8")

            await websocket.send(
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
            response = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))

    assert response["ok"] is True
    assert response["payload"] == {"path": "notes.py", "content": "print('hi')\n"}


@pytest.mark.asyncio
async def test_web_demo_rate_limits_session_creation(tmp_path: Path) -> None:
    manager = DemoSessionManager(
        access_code="dev-demo",
        sandbox_root=tmp_path / "sandboxes",
        template_repo=_template(tmp_path),
        runner_factory=_text_runner_factory("unused"),
        limits=WebDemoLimits(max_session_creates_per_minute=1),
    )
    server = WebDemoServer(session_manager=manager)

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            first = await _create_session(websocket)
            assert first["ok"] is True
            await websocket.send(
                json.dumps(
                    {
                        "id": "create-2",
                        "type": "request",
                        "method": "demo.session.create",
                        "payload": {"accessCode": "dev-demo"},
                    }
                )
            )
            response = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))

    assert response["ok"] is False
    assert response["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_web_demo_rejects_large_file_download(tmp_path: Path) -> None:
    manager = DemoSessionManager(
        access_code="dev-demo",
        sandbox_root=tmp_path / "sandboxes",
        template_repo=_template(tmp_path),
        runner_factory=_text_runner_factory("unused"),
        limits=WebDemoLimits(max_download_bytes=8),
    )
    server = WebDemoServer(session_manager=manager)

    async with server.run(host="127.0.0.1", port=0) as running:
        async with connect(running.url, proxy=None) as websocket:
            create_response = await _create_session(websocket)
            token = create_response["payload"]["sessionToken"]
            session_id = create_response["payload"]["sessionId"]
            sandbox_file = tmp_path / "sandboxes" / session_id / "notes.py"
            sandbox_file.write_text("print('hello world')\n", encoding="utf-8")

            await websocket.send(
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
            response = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))

    assert response["ok"] is False
    assert response["error"]["code"] == "FILE_TOO_LARGE"


def _text_runner_factory(message: str):
    def factory(_session: DemoSession, _approver: WebPermissionApprover):
        async def runner(
            _text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
        ) -> AsyncIterator[AgentEvent]:
            assert not abort_signal.is_triggered()
            yield AgentEvent(type=EventType.TEXT, turn=1, content=message)
            yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

        return runner

    return factory


async def _create_session(websocket) -> dict[str, object]:
    await websocket.send(
        json.dumps(
            {
                "id": "create-1",
                "type": "request",
                "method": "demo.session.create",
                "payload": {"accessCode": "dev-demo"},
            }
        )
    )
    response = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))
    assert response["ok"] is True
    return response


async def _collect_until_response(websocket, response_id: str) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    while True:
        message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))
        messages.append(message)
        if message.get("type") == "response" and message.get("id") == response_id:
            return messages
