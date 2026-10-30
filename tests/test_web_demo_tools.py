"""web demo 工具裁剪与 system prompt 刷新（移植自旧 legacy 传输测试）。"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from voice_code.web_demo.limits import WebDemoLimits
from voice_code.web_demo.runtime import (
    _build_web_demo_tools,
    _refresh_resumed_system_prompt,
)


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


def test_web_demo_refreshes_resumed_system_prompt() -> None:
    messages = [HumanMessage(content="hi"), AIMessage(content="ok")]

    refreshed = _refresh_resumed_system_prompt(messages, "new prompt")

    assert isinstance(refreshed, list)
    assert isinstance(refreshed[0], SystemMessage)
    assert refreshed[0].content == "new prompt"
