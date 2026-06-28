"""StreamingToolExecutor tests."""

from __future__ import annotations

import time

import pytest
from langchain_core.tools import tool

from voice_code.agent.types import EventType
from voice_code.tools.streaming_executor import AbortReason, StreamingToolExecutor


@pytest.mark.asyncio
async def test_completed_results_are_yielded_by_finish_order():
    @tool
    def slow_read(label: str, delay_ms: int) -> str:
        """Return after sleeping."""
        time.sleep(delay_ms / 1000)
        return f"done:{label}"

    slow_read.metadata = {"is_readonly": True, "is_concurrency_safe": True}

    executor = StreamingToolExecutor([slow_read], turn=1)
    executor.add_all([
        {"name": "slow_read", "args": {"label": "a", "delay_ms": 40}, "id": "tc_1"},
        {"name": "slow_read", "args": {"label": "b", "delay_ms": 5}, "id": "tc_2"},
    ])

    results = await executor.get_remaining_results()

    assert [event.tool_call_id for event in results] == ["tc_2", "tc_1"]
    assert all(event.type == EventType.TOOL_RESULT for event in results)


@pytest.mark.asyncio
async def test_concurrent_tools_run_before_exclusive_tool():
    timeline: list[str] = []

    @tool
    def concurrent_tool(label: str, delay_ms: int) -> str:
        """Concurrent-safe test tool."""
        timeline.append(f"start:{label}")
        time.sleep(delay_ms / 1000)
        timeline.append(f"end:{label}")
        return label

    @tool
    def exclusive_tool(label: str, delay_ms: int) -> str:
        """Exclusive test tool."""
        timeline.append(f"start:{label}")
        time.sleep(delay_ms / 1000)
        timeline.append(f"end:{label}")
        return label

    concurrent_tool.metadata = {"is_readonly": True, "is_concurrency_safe": True}
    exclusive_tool.metadata = {"is_readonly": False, "is_concurrency_safe": False}

    executor = StreamingToolExecutor([concurrent_tool, exclusive_tool], turn=1)
    executor.add_all([
        {"name": "exclusive_tool", "args": {"label": "x", "delay_ms": 5}, "id": "tc_1"},
        {"name": "concurrent_tool", "args": {"label": "a", "delay_ms": 20}, "id": "tc_2"},
        {"name": "concurrent_tool", "args": {"label": "b", "delay_ms": 10}, "id": "tc_3"},
    ])

    results = await executor.get_remaining_results()

    assert [event.tool_call_id for event in results] == ["tc_3", "tc_2", "tc_1"]
    assert timeline.index("start:x") > timeline.index("end:a")
    assert timeline.index("start:x") > timeline.index("end:b")


@pytest.mark.asyncio
async def test_abort_generates_synthetic_errors_for_queued_tools():
    @tool
    def exclusive_tool(label: str, delay_ms: int) -> str:
        """Exclusive test tool."""
        time.sleep(delay_ms / 1000)
        return label

    exclusive_tool.metadata = {"is_readonly": False, "is_concurrency_safe": False}

    executor = StreamingToolExecutor([exclusive_tool], turn=1)
    executor.add_all([
        {"name": "exclusive_tool", "args": {"label": "a", "delay_ms": 50}, "id": "tc_1"},
        {"name": "exclusive_tool", "args": {"label": "b", "delay_ms": 50}, "id": "tc_2"},
    ])

    executor.abort(AbortReason.USER_INTERRUPTED)
    immediate = executor.get_completed_results()

    assert len(immediate) == 1
    assert immediate[0].type == EventType.ERROR
    assert immediate[0].tool_call_id == "tc_2"
    assert "user_interrupted" in immediate[0].content


@pytest.mark.asyncio
async def test_result_within_limit_not_truncated():
    @tool
    def small_tool() -> str:
        """Small result tool."""
        return "hello"

    small_tool.metadata = {"is_readonly": True, "is_concurrency_safe": True, "max_result_chars": 10}

    executor = StreamingToolExecutor([small_tool], turn=1)
    executor.add_all([{"name": "small_tool", "args": {}, "id": "tc_1"}])
    results = await executor.get_remaining_results()

    assert results[0].content == "hello"


@pytest.mark.asyncio
async def test_result_exceeds_limit_truncated():
    @tool
    def big_tool() -> str:
        """Big result tool."""
        return "x" * 20

    big_tool.metadata = {"is_readonly": True, "is_concurrency_safe": True, "max_result_chars": 8}

    executor = StreamingToolExecutor([big_tool], turn=1)
    executor.add_all([{"name": "big_tool", "args": {}, "id": "tc_1"}])
    results = await executor.get_remaining_results()

    assert results[0].content.startswith("x" * 8)
    assert "[TRUNCATED:" in results[0].content
    assert "20 total chars" in results[0].content
    assert "8 shown" in results[0].content


@pytest.mark.asyncio
async def test_default_limit_when_no_metadata():
    @tool
    def no_meta_tool() -> str:
        """No metadata limit tool."""
        return "x" * 60_000

    no_meta_tool.metadata = {"is_readonly": True, "is_concurrency_safe": True}

    executor = StreamingToolExecutor([no_meta_tool], turn=1)
    executor.add_all([{"name": "no_meta_tool", "args": {}, "id": "tc_1"}])
    results = await executor.get_remaining_results()

    assert "[TRUNCATED:" in results[0].content
    assert "50000 shown" in results[0].content


@pytest.mark.asyncio
async def test_no_truncation_when_limit_is_none():
    @tool
    def unlimited_tool() -> str:
        """Unlimited result tool."""
        return "x" * 20

    unlimited_tool.metadata = {"is_readonly": True, "is_concurrency_safe": True, "max_result_chars": None}

    executor = StreamingToolExecutor([unlimited_tool], turn=1)
    executor.add_all([{"name": "unlimited_tool", "args": {}, "id": "tc_1"}])
    results = await executor.get_remaining_results()

    assert results[0].content == "x" * 20


@pytest.mark.asyncio
async def test_tool_use_error_string_not_truncated():
    @tool
    def error_tool() -> str:
        """Tool returning structured error string."""
        return "<tool_use_error>" + ("x" * 100) + "</tool_use_error>"

    error_tool.metadata = {"is_readonly": True, "is_concurrency_safe": True, "max_result_chars": 10}

    executor = StreamingToolExecutor([error_tool], turn=1)
    executor.add_all([{"name": "error_tool", "args": {}, "id": "tc_1"}])
    results = await executor.get_remaining_results()

    assert results[0].content.startswith("<tool_use_error>")
    assert "[TRUNCATED:" not in results[0].content
