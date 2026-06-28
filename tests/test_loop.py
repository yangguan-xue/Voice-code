"""消息循环单元测试 — mock LLM 和工具"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_core.tools import tool

from voice_code.agent.abort import AbortSignal
from voice_code.agent.loop import agent_loop
from voice_code.agent.types import AgentEvent, EventType
from voice_code.permissions import PermissionContext


def _make_chunks(
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "",
) -> list[AIMessageChunk]:
    """创建 AIMessageChunk 列表，最后一个 chunk 带 tool_calls（如有）。"""
    if tool_calls:
        chunks = [AIMessageChunk(content=content)]
        chunks.append(AIMessageChunk(content="", tool_calls=tool_calls))
        if finish_reason:
            chunks[-1].response_metadata = {"finish_reason": finish_reason}
        return chunks
    chunk = AIMessageChunk(content=content)
    if finish_reason:
        chunk.response_metadata = {"finish_reason": finish_reason}
    return [chunk]


def _make_model(
    *response_groups: list[AIMessageChunk],
) -> MagicMock:
    """创建 mock ChatOpenAI，每次 astream 调用返回一组 chunks。"""
    call_index = 0
    groups = list(response_groups)

    async def fake_astream(*args: object, **kwargs: object) -> AsyncGenerator[AIMessageChunk, None]:
        nonlocal call_index
        chunks = groups[call_index] if call_index < len(groups) else []
        call_index += 1
        for c in chunks:
            yield c

    model = MagicMock()
    model.astream = fake_astream
    return model


async def _async_collect(events: AsyncGenerator[AgentEvent, None]) -> list[AgentEvent]:
    result: list[AgentEvent] = []
    async for e in events:
        result.append(e)
    return result


# ============================================================
# Test 1: LLM 不调用工具，直接返回文本 → 1 轮结束
# ============================================================
@pytest.mark.asyncio
async def test_single_turn_no_tools():
    model = _make_model(_make_chunks(content="Hello, world!"))

    events = await _async_collect(
        agent_loop(
            user_input="hi",
            tools=[],
            system_prompt="You are helpful.",
            model=model,
        )
    )

    assert len(events) > 0
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "completed"
    assert events[-1].turn == 1
    texts = [e for e in events if e.type == EventType.TEXT]
    assert len(texts) > 0


# ============================================================
# Test 2: LLM 调用工具 → 执行 → 返回结果 → 文本结束
# ============================================================
@pytest.mark.asyncio
async def test_multi_turn_with_tools():
    @tool
    def echo_tool(x: str) -> str:
        """Echo the input."""
        return f"echo: {x}"
    echo_tool.metadata = {"is_readonly": True}

    turn1_chunks = _make_chunks(
        content="Let me echo.",
        tool_calls=[{"name": "echo_tool", "args": {"x": "hello"}, "id": "tc_1"}],
    )
    turn2_chunks = _make_chunks(content="Done, echo returned: echo: hello")

    model = _make_model(turn1_chunks, turn2_chunks)

    events = await _async_collect(
        agent_loop(
            user_input="echo hello",
            tools=[echo_tool],
            system_prompt="You have an echo tool.",
            model=model,
        )
    )

    types = [e.type for e in events]
    assert EventType.TOOL_CALL in types
    assert EventType.TOOL_RESULT in types
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "completed"
    assert events[-1].turn == 2


# ============================================================
# Test 3: 超过 max_turns → 截断
# ============================================================
@pytest.mark.asyncio
async def test_max_turns_exceeded():
    tool_chunks = _make_chunks(
        content="calling...",
        tool_calls=[{"name": "echo_tool", "args": {"x": "x"}, "id": "tc_1"}],
    )
    @tool
    def echo_tool(x: str) -> str:
        """Echo the input."""
        return f"echo: {x}"
    echo_tool.metadata = {"is_readonly": True}

    # 每轮都返回 tool_call，永远不结束
    model = _make_model(*([tool_chunks] * 10))

    events = await _async_collect(
        agent_loop(
            user_input="loop forever",
            tools=[echo_tool],
            system_prompt="You have echo.",
            model=model,
            max_turns=3,
        )
    )

    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "max_turns"
    assert events[-1].turn == 3


# ============================================================
# Test 4: LLM 请求不存在的工具 → 错误 ToolMessage → 重试
# ============================================================
@pytest.mark.asyncio
async def test_tool_not_found():
    turn1_chunks = _make_chunks(
        content="Let me use unknown tool.",
        tool_calls=[{"name": "nonexistent", "args": {}, "id": "tc_1"}],
    )
    turn2_chunks = _make_chunks(content="OK, the tool doesn't exist. Let me try something else.")

    model = _make_model(turn1_chunks, turn2_chunks)

    events = await _async_collect(
        agent_loop(
            user_input="use unknown tool",
            tools=[],
            system_prompt="You are helpful.",
            model=model,
        )
    )

    # 应该有 ERROR 事件（工具未找到）
    errors = [e for e in events if e.type == EventType.ERROR]
    assert len(errors) > 0
    assert "No such tool available" in errors[0].content
    # 最终正常结束
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "completed"


# ============================================================
# Test 5: 空输入 → 直接结束
# ============================================================
@pytest.mark.asyncio
async def test_empty_user_input():
    model = _make_model()
    events = await _async_collect(
        agent_loop(
            user_input="   ",
            tools=[],
            system_prompt="You are helpful.",
            model=model,
        )
    )

    assert len(events) == 1
    assert events[0].type == EventType.FINISH
    assert events[0].finish_reason == "error"


@pytest.mark.asyncio
async def test_llm_stream_timeout():
    async def fake_astream(*args: object, **kwargs: object) -> AsyncGenerator[AIMessageChunk, None]:
        await asyncio.sleep(0.05)
        if False:
            yield AIMessageChunk(content="")

    model = MagicMock()
    model.astream = fake_astream

    events = await _async_collect(
        agent_loop(
            user_input="hi",
            tools=[],
            system_prompt="You are helpful.",
            model=model,
            llm_timeout_seconds=0.01,
        )
    )

    assert len(events) == 2
    assert events[0].type == EventType.ERROR
    assert "timed out" in events[0].content
    assert events[1].type == EventType.FINISH
    assert events[1].finish_reason == "error"


@pytest.mark.asyncio
async def test_bash_search_redirected_to_dedicated_tools():
    @tool
    def bash(command: str, description: str = "") -> str:
        """Execute a shell command."""
        return "should not run"

    bash.metadata = {"is_readonly": False}

    turn1_chunks = _make_chunks(
        content="I will inspect the project structure.",
        tool_calls=[{"name": "bash", "args": {"command": "find . -type f"}, "id": "tc_1"}],
    )
    turn2_chunks = _make_chunks(content="I should use glob instead.")
    model = _make_model(turn1_chunks, turn2_chunks)

    events = await _async_collect(
        agent_loop(
            user_input="当前项目结构？",
            tools=[bash],
            system_prompt="Use tools correctly.",
            model=model,
        )
    )

    errors = [e for e in events if e.type == EventType.ERROR]
    assert errors
    assert "Do not use Bash for file discovery" in errors[0].content
    assert all(
        not (e.type == EventType.TOOL_RESULT and e.content == "should not run")
        for e in events
    )
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "completed"


@pytest.mark.asyncio
async def test_normal_bash_tool_call_is_executed():
    @tool
    def bash(command: str, description: str = "") -> str:
        """Execute a shell command."""
        return "bash ok"

    bash.metadata = {"is_readonly": False}

    turn1_chunks = _make_chunks(
        content="Run bash.",
        tool_calls=[{"name": "bash", "args": {"command": "pwd"}, "id": "tc_1"}],
    )
    turn2_chunks = _make_chunks(content="Done.")
    model = _make_model(turn1_chunks, turn2_chunks)

    events = await _async_collect(
        agent_loop(
            user_input="run pwd",
            tools=[bash],
            system_prompt="Use tools correctly.",
            model=model,
            permission_context=PermissionContext(mode="bypassPermissions"),
        )
    )

    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert tool_results
    assert tool_results[0].content == "bash ok"
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "completed"


@pytest.mark.asyncio
async def test_permission_context_is_honored_for_write_tools():
    @tool
    def write(file_path: str, content: str) -> str:
        """Write content to a file."""
        return "write ok"

    write.metadata = {"is_readonly": False}

    turn1_chunks = _make_chunks(
        content="Writing now.",
        tool_calls=[{"name": "write", "args": {"file_path": "/tmp/x", "content": "y"}, "id": "tc_1"}],
    )
    turn2_chunks = _make_chunks(content="Done.")
    model = _make_model(turn1_chunks, turn2_chunks)

    events = await _async_collect(
        agent_loop(
            user_input="write it",
            tools=[write],
            system_prompt="Use tools.",
            model=model,
            permission_context=PermissionContext(mode="bypassPermissions"),
        )
    )

    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert tool_results
    assert tool_results[0].content == "write ok"
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "completed"


@pytest.mark.asyncio
async def test_abort_during_streaming_finishes_interrupted():
    abort_sig = AbortSignal()

    async def fake_astream(*args: object, **kwargs: object) -> AsyncGenerator[AIMessageChunk, None]:
        yield AIMessageChunk(content="hello")
        abort_sig.trigger()
        await asyncio.sleep(0)
        yield AIMessageChunk(content="world")

    model = MagicMock()
    model.astream = fake_astream

    events = await _async_collect(
        agent_loop(
            user_input="hi",
            tools=[],
            system_prompt="You are helpful.",
            model=model,
            abort_signal=abort_sig,
        )
    )

    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "interrupted"


@pytest.mark.asyncio
async def test_abort_before_tool_execution_skips_tools():
    abort_sig = AbortSignal()

    @tool
    def write(file_path: str, content: str) -> str:
        """Write content to a file."""
        raise AssertionError("tool should not run")

    write.metadata = {"is_readonly": False}

    turn1_chunks = _make_chunks(
        content="Writing now.",
        tool_calls=[{"name": "write", "args": {"file_path": "/tmp/x", "content": "y"}, "id": "tc_1"}],
    )

    async def fake_astream(*args: object, **kwargs: object) -> AsyncGenerator[AIMessageChunk, None]:
        for chunk in turn1_chunks:
            yield chunk
        abort_sig.trigger()

    model = MagicMock()
    model.astream = fake_astream

    events = await _async_collect(
        agent_loop(
            user_input="write it",
            tools=[write],
            system_prompt="Use tools.",
            model=model,
            permission_context=PermissionContext(mode="bypassPermissions"),
            abort_signal=abort_sig,
        )
    )

    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "interrupted"


@pytest.mark.asyncio
async def test_abort_during_tool_execution_yields_error_and_finishes():
    abort_sig = AbortSignal()

    @tool
    def slow_tool(delay_ms: int) -> str:
        """Sleep for a bit."""
        time.sleep(delay_ms / 1000)
        return "done"

    slow_tool.metadata = {"is_readonly": True, "is_concurrency_safe": True}

    turn1_chunks = _make_chunks(
        content="Running tool.",
        tool_calls=[{"name": "slow_tool", "args": {"delay_ms": 100}, "id": "tc_1"}],
    )
    model = _make_model(turn1_chunks)

    async def run() -> list[AgentEvent]:
        await asyncio.sleep(0.02)
        abort_sig.trigger()
        return []

    trigger_task = asyncio.create_task(run())
    events = await _async_collect(
        agent_loop(
            user_input="run tool",
            tools=[slow_tool],
            system_prompt="Use tools.",
            model=model,
            abort_signal=abort_sig,
        )
    )
    await trigger_task

    assert any(e.type == EventType.ERROR and "aborted" in e.content for e in events)
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "interrupted"


@pytest.mark.asyncio
async def test_context_overflow_triggers_compact_and_retry(monkeypatch: pytest.MonkeyPatch):
    turn2_chunks = _make_chunks(content="Recovered after compact.")
    model = _make_model(turn2_chunks)
    original_astream = model.astream

    call_count = 0

    async def fake_astream(*args: object, **kwargs: object) -> AsyncGenerator[AIMessageChunk, None]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("maximum context length exceeded")
        async for chunk in original_astream(*args, **kwargs):
            yield chunk

    model.astream = fake_astream

    async def fake_compact(messages: list[object], model_obj: object) -> list[object]:
        return messages[:-1]

    monkeypatch.setattr("voice_code.agent.loop.try_reactive_compact", fake_compact)

    events = await _async_collect(
        agent_loop(
            user_input="hi",
            tools=[],
            system_prompt="You are helpful.",
            model=model,
        )
    )

    assert any("reactive" in e.content for e in events if e.type == EventType.ERROR)
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "completed"


@pytest.mark.asyncio
async def test_context_overflow_twice_gives_up(monkeypatch: pytest.MonkeyPatch):
    async def fake_astream(*args: object, **kwargs: object) -> AsyncGenerator[AIMessageChunk, None]:
        raise ValueError("context window exceeded")
        if False:
            yield AIMessageChunk(content="")

    model = MagicMock()
    model.astream = fake_astream

    async def fake_compact(messages: list[object], model_obj: object) -> list[object]:
        return messages[:-1]

    monkeypatch.setattr("voice_code.agent.loop.try_reactive_compact", fake_compact)

    events = await _async_collect(
        agent_loop(
            user_input="hi",
            tools=[],
            system_prompt="You are helpful.",
            model=model,
        )
    )

    assert sum(1 for e in events if e.type == EventType.ERROR and "reactive" in e.content) == 1
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "error"


@pytest.mark.asyncio
async def test_non_context_error_not_retried():
    async def fake_astream(*args: object, **kwargs: object) -> AsyncGenerator[AIMessageChunk, None]:
        raise ValueError("auth failed")
        if False:
            yield AIMessageChunk(content="")

    model = MagicMock()
    model.astream = fake_astream

    events = await _async_collect(
        agent_loop(
            user_input="hi",
            tools=[],
            system_prompt="You are helpful.",
            model=model,
        )
    )

    assert not any("reactive" in e.content for e in events if e.type == EventType.ERROR)
    assert any(e.type == EventType.ERROR and e.status == "generic_error" for e in events)
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "error"


@pytest.mark.asyncio
async def test_resume_messages_continue_conversation():
    model = _make_model(_make_chunks(content="continued"))
    events = await _async_collect(
        agent_loop(
            user_input="new question",
            tools=[],
            system_prompt="ignored",
            model=model,
            resume_messages=[
                SystemMessage(content="sys"),
                HumanMessage(content="old question"),
                AIMessage(content="old answer"),
            ],
        )
    )

    assert any(e.type == EventType.TEXT and "continued" in e.content for e in events)
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "completed"


@pytest.mark.asyncio
async def test_truncated_response_triggers_resume():
    model = _make_model(
        _make_chunks(content="partial", finish_reason="length"),
        _make_chunks(content=" completed"),
    )

    events = await _async_collect(
        agent_loop(
            user_input="hi",
            tools=[],
            system_prompt="You are helpful.",
            model=model,
        )
    )

    assert any("max_output_tokens hit, resuming" in e.content for e in events if e.type == EventType.ERROR)
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "completed"


@pytest.mark.asyncio
async def test_truncated_response_gives_up_after_max_recovery():
    model = _make_model(
        _make_chunks(content="a", finish_reason="length"),
        _make_chunks(content="b", finish_reason="length"),
        _make_chunks(content="c", finish_reason="length"),
    )

    events = await _async_collect(
        agent_loop(
            user_input="hi",
            tools=[],
            system_prompt="You are helpful.",
            model=model,
        )
    )

    assert any("Output token limit hit too many times" in e.content for e in events if e.type == EventType.ERROR)
    assert events[-1].type == EventType.FINISH
    assert events[-1].finish_reason == "error"


@pytest.mark.asyncio
async def test_model_overloaded_switches_to_fallback():
    class OverloadedError(Exception):
        status_code = 529

    primary = MagicMock()

    async def primary_astream(*args: object, **kwargs: object) -> AsyncGenerator[AIMessageChunk, None]:
        raise OverloadedError("overloaded")
        if False:
            yield AIMessageChunk(content="")

    fallback = _make_model(_make_chunks(content="fallback ok"))
    primary.astream = primary_astream

    events = await _async_collect(
        agent_loop(
            user_input="hi",
            tools=[],
            system_prompt="You are helpful.",
            model=primary,
            fallback_model=fallback,
        )
    )

    assert any("Switched to fallback model" in e.content for e in events if e.type == EventType.ERROR)
    assert any(e.type == EventType.TEXT and "fallback ok" in e.content for e in events)
    assert any(e.type == EventType.ERROR and e.status == "fallback" for e in events)


@pytest.mark.asyncio
async def test_compact_resume_and_permission_events_have_status():
    @tool
    def write(file_path: str, content: str) -> str:
        """Write content to a file."""
        return "write ok"

    write.metadata = {"is_readonly": False}

    model = _make_model(
        _make_chunks(content="a", finish_reason="length"),
        _make_chunks(
            content="trying write",
            tool_calls=[{"name": "write", "args": {"file_path": "/tmp/x", "content": "y"}, "id": "tc_1"}],
        ),
    )

    events = await _async_collect(
        agent_loop(
            user_input="hi",
            tools=[write],
            system_prompt="You are helpful.",
            model=model,
            permission_context=PermissionContext(mode="dontAsk"),
        )
    )

    error_statuses = {e.status for e in events if e.type == EventType.ERROR}
    assert "resume" in error_statuses
    assert "permission_denied" in error_statuses


@pytest.mark.asyncio
async def test_bash_search_redirect_has_generic_error_status():
    @tool
    def bash(command: str, description: str = "") -> str:
        """Execute a shell command."""
        return "should not run"

    bash.metadata = {"is_readonly": False}

    model = _make_model(
        _make_chunks(
            content="searching",
            tool_calls=[{"name": "bash", "args": {"command": "find . -type f"}, "id": "tc_1"}],
        ),
        _make_chunks(content="done"),
    )

    events = await _async_collect(
        agent_loop(
            user_input="search",
            tools=[bash],
            system_prompt="Use tools correctly.",
            model=model,
        )
    )

    assert any(
        e.type == EventType.ERROR and e.status == "generic_error"
        for e in events
    )
