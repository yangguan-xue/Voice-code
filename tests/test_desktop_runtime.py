from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from voice_code.agent.abort import AbortSignal
from voice_code.agent.types import AgentEvent, EventType
from voice_code.desktop.runtime import (
    DesktopRuntimeActions,
    _git_context,
    _messages_to_conversation,
    make_desktop_agent_runner,
)
from voice_code.permissions import PermissionContext


@pytest.mark.asyncio
async def test_make_desktop_agent_runner_streams_agent_loop_in_worker(monkeypatch) -> None:
    worker_thread_ids: list[int] = []
    captured: dict[str, object] = {}
    permission_context = PermissionContext(session_id="session-1")
    runtime = SimpleNamespace(
        tools=["tool"],
        prompt="system prompt",
        model="model",
        fallback_model="fallback",
        resume_messages=[
            SystemMessage(content="system prompt"),
            HumanMessage(content="resume"),
        ],
        transcript_writer=SimpleNamespace(read_all_messages=lambda: ["resume", "updated"]),
        session_id="session-1",
        memory_service="memory",
        memory_project_key="project",
    )

    async def fake_agent_loop(
        user_input: str,
        tools: list[object],
        system_prompt: str,
        model: object,
        **kwargs: object,
    ) -> AsyncIterator[AgentEvent]:
        worker_thread_ids.append(threading.get_ident())
        captured.update(
            user_input=user_input,
            tools=tools,
            system_prompt=system_prompt,
            model=model,
            kwargs=kwargs,
            permission_mode=kwargs["permission_context"].mode,
        )
        yield AgentEvent(type=EventType.TEXT, turn=1, content="real runtime")
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

    monkeypatch.setattr("voice_code.desktop.runtime.agent_loop", fake_agent_loop)

    runner = make_desktop_agent_runner(
        runtime,  # type: ignore[arg-type]
        permission_context=permission_context,
    )
    events = [
        event
        async for event in runner(
            "hello",
            abort_signal=AbortSignal(),
            permission_mode="dontAsk",
        )
    ]

    assert worker_thread_ids
    assert worker_thread_ids[0] != threading.get_ident()
    assert [event.content for event in events] == ["real runtime", ""]
    assert captured["user_input"] == "hello"
    assert captured["tools"] == ["tool"]
    assert captured["system_prompt"] == "system prompt"
    assert captured["model"] == "model"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["permission_context"] is permission_context
    assert captured["permission_mode"] == "dontAsk"
    assert permission_context.mode == "default"
    assert isinstance(kwargs["abort_signal"], AbortSignal)
    resume_messages = kwargs["resume_messages"]
    assert isinstance(resume_messages, list)
    assert [message.content for message in resume_messages] == ["system prompt", "resume"]
    assert kwargs["transcript_writer"] is runtime.transcript_writer
    assert kwargs["fallback_model"] == "fallback"
    assert kwargs["runtime_session_id"] == "session-1"
    assert kwargs["memory_service"] == "memory"
    assert kwargs["memory_project_key"] == "project"
    assert runtime.resume_messages == ["resume", "updated"]


@pytest.mark.asyncio
async def test_make_desktop_agent_runner_refreshes_resume_messages_after_turn(
    monkeypatch,
) -> None:
    refreshed_messages = ["system", "human", "ai", "tool"]
    permission_context = PermissionContext(session_id="session-1")
    runtime = SimpleNamespace(
        tools=[],
        prompt="system prompt",
        model="model",
        fallback_model=None,
        resume_messages=None,
        transcript_writer=SimpleNamespace(read_all_messages=lambda: refreshed_messages),
        session_id="session-1",
        memory_service=None,
        memory_project_key="project",
    )

    async def fake_agent_loop(
        _user_input: str,
        _tools: list[object],
        _system_prompt: str,
        _model: object,
        **_kwargs: object,
    ) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type=EventType.TEXT, turn=1, content="done")
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

    monkeypatch.setattr("voice_code.desktop.runtime.agent_loop", fake_agent_loop)

    runner = make_desktop_agent_runner(
        runtime,  # type: ignore[arg-type]
        permission_context=permission_context,
    )

    events = [
        event
        async for event in runner(
            "刚才那个文档路径呢",
            abort_signal=AbortSignal(),
        )
    ]

    assert [event.content for event in events] == ["done", ""]
    assert runtime.resume_messages == refreshed_messages


@pytest.mark.asyncio
async def test_make_desktop_agent_runner_replaces_stale_resumed_system_prompt(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    permission_context = PermissionContext(session_id="session-1")
    runtime = SimpleNamespace(
        tools=[],
        prompt="new prompt says gpt-company",
        model="model",
        fallback_model=None,
        resume_messages=[
            SystemMessage(content="old prompt says deepseek-v4-pro"),
            HumanMessage(content="hello"),
            AIMessage(content="hi"),
        ],
        transcript_writer=SimpleNamespace(read_all_messages=lambda: []),
        session_id="session-1",
        memory_service=None,
        memory_project_key="project",
    )

    async def fake_agent_loop(
        _user_input: str,
        _tools: list[object],
        _system_prompt: str,
        _model: object,
        **kwargs: object,
    ) -> AsyncIterator[AgentEvent]:
        captured["resume_messages"] = kwargs["resume_messages"]
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

    monkeypatch.setattr("voice_code.desktop.runtime.agent_loop", fake_agent_loop)

    runner = make_desktop_agent_runner(
        runtime,  # type: ignore[arg-type]
        permission_context=permission_context,
    )
    _ = [event async for event in runner("你是什么模型", abort_signal=AbortSignal())]

    messages = captured["resume_messages"]
    assert isinstance(messages, list)
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == "new prompt says gpt-company"


def test_messages_to_conversation_restores_text_and_tools() -> None:
    messages = [
        HumanMessage(content="检查项目"),
        AIMessage(
            content="我先读取文件。",
            tool_calls=[{"id": "call-1", "name": "read", "args": {"path": "README.md"}}],
        ),
        ToolMessage(content="project docs", tool_call_id="call-1", name="read"),
        AIMessage(content="README 已读取。"),
        HumanMessage(content="继续"),
        AIMessage(content="好的。"),
    ]

    conversation = _messages_to_conversation("saved-session", messages)

    assert conversation["activeTurnId"] is None
    assert len(conversation["turns"]) == 2
    first = conversation["turns"][0]
    assert first["userText"] == "检查项目"
    assert first["assistantText"] == "我先读取文件。\n\nREADME 已读取。"
    assert first["tools"] == [
        {
            "id": "call-1",
            "name": "read",
            "args": {"path": "README.md"},
            "result": "project docs",
            "resultPreview": "project docs",
            "status": "completed",
        }
    ]


def test_git_context_reports_dirty_workspace(tmp_path) -> None:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "desktop@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Desktop Test"],
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "initial"], check=True)

    assert _git_context(str(tmp_path))["isDirty"] is False

    tracked.write_text("changed\n", encoding="utf-8")

    assert _git_context(str(tmp_path))["isDirty"] is True


@pytest.mark.asyncio
async def test_configure_model_replaces_runtime_model(monkeypatch) -> None:
    configured = SimpleNamespace(model_name="gpt-company")
    runtime = SimpleNamespace(
        cwd="D:/workspace/project",
        tools=[],
        model=SimpleNamespace(model_name="old-model"),
        fallback_model="fallback",
        fallback_profile="fallback-profile",
        prompt="old prompt",
    )
    actions = DesktopRuntimeActions.__new__(DesktopRuntimeActions)
    actions.runtime = runtime
    actions.bridge = SimpleNamespace(is_busy=False)
    actions.active_profile = "builtin"

    monkeypatch.setattr(
        "voice_code.desktop.runtime.model_config.init_model",
        lambda **kwargs: configured,
    )

    async def fake_prompt(**kwargs):
        assert kwargs["model_name"] == "gpt-company"
        return "custom prompt"

    monkeypatch.setattr("voice_code.desktop.runtime.build_workspace_prompt", fake_prompt)

    result = await actions.configure_model(
        {
            "id": "custom-company",
            "displayName": "Company GPT",
            "baseUrl": "https://llm.example.com/v1",
            "apiKey": "secret",
            "modelName": "gpt-company",
        }
    )

    assert result == {"profile": "custom-company", "modelName": "gpt-company"}
    assert runtime.model is configured
    assert runtime.fallback_model is None
    assert runtime.fallback_profile is None
    assert runtime.prompt == "custom prompt"


@pytest.mark.asyncio
async def test_configured_model_name_is_sent_to_openai_compatible_api(monkeypatch) -> None:
    received: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            received.update(json.loads(self.rfile.read(length)))
            response = json.dumps(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": received["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime = SimpleNamespace(
        cwd="D:/workspace/project",
        tools=[],
        model=SimpleNamespace(model_name="old-model"),
        fallback_model=None,
        fallback_profile=None,
        prompt="old prompt",
    )
    actions = DesktopRuntimeActions.__new__(DesktopRuntimeActions)
    actions.runtime = runtime
    actions.bridge = SimpleNamespace(is_busy=False)
    actions.active_profile = "builtin"

    async def fake_prompt(**_kwargs):
        return "custom prompt"

    monkeypatch.setattr("voice_code.desktop.runtime.build_workspace_prompt", fake_prompt)
    try:
        await actions.configure_model(
            {
                "id": "custom-company",
                "displayName": "Company GPT",
                "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                "apiKey": "secret",
                "modelName": "gpt-company",
            }
        )
        response = await runtime.model.ainvoke("ping")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert response.content == "ok"
    assert received["model"] == "gpt-company"
