"""上下文压缩测试"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from reasoning_agent.compact.boundary import make_compact_boundary
from reasoning_agent.compact.context_collapse import collapse_old_turns
from reasoning_agent.compact.micro_compact import apply_micro_compact
from reasoning_agent.compact.prompt import format_compact_summary, get_compact_prompt
from reasoning_agent.compact.reactive_compact import is_context_overflow_error
from reasoning_agent.compact.snip import snip_compact
from reasoning_agent.compact.token_count import rough_token_count_for_messages


def test_rough_token_count():
    """粗略 token 估算。"""
    messages = [
        SystemMessage(content="hello world"),
        HumanMessage(content="this is a test"),
        ToolMessage(content="result", tool_call_id="1"),
    ]
    count = rough_token_count_for_messages(messages)
    assert count > 0
    assert count < 50  # 5 + 3 + 1 = 9 tokens by rough estimate


def test_rough_token_count_empty():
    assert rough_token_count_for_messages([]) == 0


def test_micro_compact_no_effect():
    """工具结果少时不触发清除。"""
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="hi"),
        ToolMessage(content="r1", tool_call_id="1"),
        HumanMessage(content="next"),
    ]
    result, freed = apply_micro_compact(messages, keep_recent=5)
    assert result == messages  # only 2 tool results, < 5
    assert freed == 0


def test_micro_compact_default_keeps_more_recent_tool_results():
    """默认配置应尽量保留更多最近工具输出，减少“失忆感”."""
    messages = [SystemMessage(content="sys")]
    for i in range(12):
        messages.append(ToolMessage(content=f"result {i}", tool_call_id=str(i)))
    messages.append(HumanMessage(content="last user"))

    result, freed = apply_micro_compact(messages)

    assert result == messages
    assert freed == 0


def test_micro_compact_clears_old():
    """旧工具结果被清除。"""
    messages = []
    messages.append(SystemMessage(content="sys"))
    for i in range(10):
        messages.append(ToolMessage(content=f"result {i}", tool_call_id=str(i)))
    messages.append(HumanMessage(content="last user"))

    result, freed = apply_micro_compact(messages, keep_recent=3)

    cleared = sum(1 for m in result if isinstance(m, ToolMessage) and "Old" in str(m.content))
    kept = sum(1 for m in result if isinstance(m, ToolMessage) and "result" in str(m.content))
    assert cleared == 7  # 10 - 3 = 7 cleared
    assert kept == 3
    assert freed > 0


def test_micro_compact_ignores_after_last_user():
    """最后一条用户消息之后的工具结果不被清除。"""
    messages = [
        SystemMessage(content="sys"),
        ToolMessage(content="old result", tool_call_id="1"),
        ToolMessage(content="old result 2", tool_call_id="2"),
        HumanMessage(content="user msg"),
        ToolMessage(content="recent result", tool_call_id="3"),
    ]
    result, freed = apply_micro_compact(messages, keep_recent=1)
    # "recent result" (after last user) should NOT be cleared
    recent = [m for m in result if isinstance(m, ToolMessage) and "recent" in str(m.content)]
    assert len(recent) == 1
    assert freed >= 0


def test_compact_prompt():
    """Compact prompt 包含必要部分。"""
    prompt = get_compact_prompt()
    assert "summary" in prompt.lower()
    assert "Primary Request" in prompt
    assert "Files and Code Sections" in prompt
    assert "CRITICAL" in prompt


def test_format_compact_summary():
    """解析 <summary> 标签。"""
    raw = "<analysis>blah</analysis>\n<summary>\nhello world\n</summary>"
    result = format_compact_summary(raw)
    assert "hello world" in result
    assert "blah" not in result


def test_format_compact_summary_no_tags():
    """无标签时返回原文。"""
    result = format_compact_summary("just text")
    assert "just text" in result


def test_compact_boundary():
    """边界消息格式正确。"""
    msg = make_compact_boundary("auto", 10000)
    assert "Conversation compacted" in str(msg.content)
    assert "10000" in str(msg.content)


def test_is_context_overflow_error_with_string_match():
    err = ValueError("maximum context length exceeded")
    assert is_context_overflow_error(err) is True


def test_is_context_overflow_error_with_non_overflow_error():
    err = ValueError("authentication failed")
    assert is_context_overflow_error(err) is False


def test_is_context_overflow_error_with_413_status():
    err = SimpleNamespace(status_code=413)
    assert is_context_overflow_error(err) is True


def test_snip_no_removal_when_under_threshold():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="u1"),
        ToolMessage(content="r1", tool_call_id="1"),
        HumanMessage(content="u2"),
        ToolMessage(content="r2", tool_call_id="2"),
    ]
    result, stats = snip_compact(messages, keep_recent_turns=3)
    assert result == messages
    assert stats.active is False


def test_snip_removes_old_tool_messages_and_inserts_boundary():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="u1"),
        ToolMessage(content="r1", tool_call_id="1"),
        HumanMessage(content="u2"),
        ToolMessage(content="r2", tool_call_id="2"),
        HumanMessage(content="u3"),
        ToolMessage(content="r3", tool_call_id="3"),
        HumanMessage(content="u4"),
        ToolMessage(content="r4", tool_call_id="4"),
    ]
    result, stats = snip_compact(messages, keep_recent_turns=2)

    assert stats.tokens_freed > 0
    assert "old tool results removed" in stats.details
    assert any(isinstance(m, HumanMessage) and "[SNIP:" in str(m.content) for m in result)
    tool_ids = [m.tool_call_id for m in result if isinstance(m, ToolMessage)]
    assert tool_ids == ["3", "4"]


def test_snip_preserves_non_tool_messages():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="u1"),
        HumanMessage(content="u2"),
        HumanMessage(content="u3"),
    ]
    result, stats = snip_compact(messages, keep_recent_turns=1)
    assert result == messages
    assert stats.active is False


def test_context_collapse_no_collapse_when_under_min_turns():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="u1"),
        AIMessage(content="ok", tool_calls=[{"name": "read", "args": {}, "id": "1"}]),
        ToolMessage(content="r1", tool_call_id="1"),
    ]
    result, stats = collapse_old_turns(messages, min_turns_before_collapse=6)
    assert result == messages
    assert stats.active is False


def test_context_collapse_default_waits_for_more_turns():
    """默认配置应在较多轮次后再折叠旧 turn。"""
    messages = [SystemMessage(content="sys")]
    for i in range(1, 10):
        messages.extend(
            [
                HumanMessage(content=f"u{i}"),
                AIMessage(
                    content=f"answer {i}",
                    tool_calls=[{"name": "read", "args": {}, "id": str(i)}],
                ),
                ToolMessage(content=f"result {i}", tool_call_id=str(i)),
            ]
        )

    result, stats = collapse_old_turns(messages)

    assert result == messages
    assert stats.active is False


def test_context_collapse_collapses_read_only_turns():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="u1"),
        AIMessage(content="文件有 2 个函数", tool_calls=[{"name": "read", "args": {}, "id": "1"}]),
        ToolMessage(content="full file", tool_call_id="1"),
        HumanMessage(content="u2"),
        AIMessage(content="找到 5 处 TODO", tool_calls=[{"name": "grep", "args": {}, "id": "2"}]),
        ToolMessage(content="matches", tool_call_id="2"),
        HumanMessage(content="u3"),
        AIMessage(content="recent", tool_calls=[{"name": "read", "args": {}, "id": "3"}]),
        ToolMessage(content="recent file", tool_call_id="3"),
    ]
    result, stats = collapse_old_turns(messages, min_turns_before_collapse=2)
    summaries = [
        m
        for m in result
        if isinstance(m, HumanMessage) and str(m.content).startswith("[Turn ")
    ]
    assert len(summaries) == 1
    assert "read" in str(summaries[0].content)
    assert "文件有 2 个函数" in str(summaries[0].content)
    assert stats.active is True
    assert "turns collapsed" in stats.details


def test_context_collapse_preserves_write_turns():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="u1"),
        AIMessage(content="editing", tool_calls=[{"name": "edit", "args": {}, "id": "1"}]),
        ToolMessage(content="edited", tool_call_id="1"),
        HumanMessage(content="u2"),
        AIMessage(content="recent", tool_calls=[{"name": "read", "args": {}, "id": "2"}]),
        ToolMessage(content="recent file", tool_call_id="2"),
    ]
    result, stats = collapse_old_turns(messages, min_turns_before_collapse=1)
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "1" for m in result)
    assert stats.active is False


def test_context_collapse_preserves_recent_turns():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="u1"),
        AIMessage(content="older", tool_calls=[{"name": "read", "args": {}, "id": "1"}]),
        ToolMessage(content="old file", tool_call_id="1"),
        HumanMessage(content="u2"),
        AIMessage(content="newer", tool_calls=[{"name": "read", "args": {}, "id": "2"}]),
        ToolMessage(content="new file", tool_call_id="2"),
    ]
    result, stats = collapse_old_turns(messages, min_turns_before_collapse=1)
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "2" for m in result)
    assert stats.active is True
