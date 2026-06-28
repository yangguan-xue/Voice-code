"""Agent 消息循环 — 手写 while 循环，astream 流式调用 LLM。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from reasoning_agent.agent.abort import AbortSignal
from reasoning_agent.agent.error_recovery import (
    MAX_OUTPUT_RECOVERY,
    MAX_OUTPUT_RECOVERY_MSG,
    get_finish_reason,
    is_model_overloaded_error,
)
from reasoning_agent.agent.types import AgentEvent, EventType
from reasoning_agent.compact import (
    apply_micro_compact,
    collapse_old_turns,
    compact_conversation,
    is_context_overflow_error,
    should_auto_compact,
    snip_compact,
    try_reactive_compact,
)
from reasoning_agent.permissions import (
    PermissionBehavior,
    PermissionContext,
    can_use_tool,
)
from reasoning_agent.session.transcript import TranscriptWriter
from reasoning_agent.tools import find_tool_by_name
from reasoning_agent.tools.streaming_executor import AbortReason, StreamingToolExecutor

logger = logging.getLogger(__name__)

_ERROR_TOOL_NOT_FOUND = (
    "<tool_use_error>Error: No such tool available: {name}</tool_use_error>"
)
_ERROR_TOOL_EXEC = (
    "<tool_use_error>Error calling tool ({name}): {error}</tool_use_error>"
)
_ERROR_PERMISSION_DENIED = (
    "Error: The user doesn't want to proceed with this tool use. "
    "The tool use was rejected. STOP what you are doing and wait for "
    "the user to tell you how to proceed."
)
_ERROR_BASH_SEARCH_REDIRECT = (
    "<tool_use_error>Error: Do not use Bash for file discovery, directory "
    "trees, or content search when dedicated tools are available. Use "
    "`glob` for project structure and filename searches, and use `grep` "
    "for content searches.</tool_use_error>"
)
_ERROR_TOOL_ABORTED = (
    "<tool_use_error>Error: Tool execution aborted: {reason}</tool_use_error>"
)


def _parse_tool_call(tc: object) -> dict[str, Any]:
    """将流式合并后的 tool_call 项归一化为 dict。

    LangChain 流式合并后 tool_calls 可能是 dict 或 ToolCallChunk，
    且 args 可能是 JSON 字符串（流式片段累积）或已解析的 dict。
    """
    if isinstance(tc, dict):
        name = tc.get("name", "")
        args = tc.get("args", {})
        tc_id = tc.get("id", "")
    else:
        name = getattr(tc, "name", "") or ""
        args = getattr(tc, "args", {}) or {}
        tc_id = getattr(tc, "id", "") or ""

    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    return {"name": str(name), "args": args, "id": str(tc_id)}


def _should_redirect_bash_command(command: str) -> bool:
    """拦截本该用 Glob/Grep 的 Bash 搜索类命令。"""
    normalized = " ".join(command.strip().lower().split())
    if not normalized:
        return False

    prefixes = (
        "find ",
        "ls ",
        "tree",
        "grep ",
        "rg ",
        "fd ",
    )
    return normalized.startswith(prefixes)


def _get_reasoning(chunk: object) -> str:
    """Extract DeepSeek reasoning_content from a streaming chunk."""
    # LangChain ChatOpenAI stores extra fields in additional_kwargs
    additional = getattr(chunk, "additional_kwargs", None) or {}
    if isinstance(additional, dict):
        reasoning = additional.get("reasoning_content", "")
        if reasoning:
            return str(reasoning)
    return ""


def _tools_to_openai_dicts(tools: list[BaseTool]) -> list[dict[str, object]]:
    """将 LangChain BaseTool 列表转换为 OpenAI API dict 格式。"""
    result: list[dict[str, object]] = []
    for t in tools:
        # 获取 JSON Schema
        if hasattr(t, "args_schema") and t.args_schema is not None:
            schema = t.args_schema.model_json_schema()  # type: ignore[union-attr]
        else:
            schema = {"type": "object", "properties": {}, "required": []}

        # 清理 schema 中的 $defs 等（DeepSeek 不兼容）
        params: dict[str, object] = {
            "type": schema.get("type", "object"),
            "properties": schema.get("properties", {}),
        }
        if schema.get("required"):
            params["required"] = schema["required"]

        result.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": params,
            },
        })
    return result


async def agent_loop(
    user_input: str,
    tools: list[BaseTool],
    system_prompt: str,
    model: ChatOpenAI,
    *,
    max_turns: int = 30,
    llm_timeout_seconds: float = 90.0,
    permission_context: PermissionContext | None = None,
    abort_signal: AbortSignal | None = None,
    resume_messages: list[Any] | None = None,
    transcript_writer: TranscriptWriter | None = None,
    fallback_model: ChatOpenAI | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """执行 Agent 消息循环 (astream 流式)。

    每轮:
      1. astream 调用 LLM, yield TEXT 事件（逐 chunk）
      2. 合并 chunks → 完整 AIMessage
      3. 如有 tool_calls → 执行工具 → yield TOOL_CALL / TOOL_RESULT / ERROR
      4. 无 tool_calls → yield FINISH 结束
      5. 超 max_turns → yield FINISH(max_turns)
    """
    if not user_input.strip():
        yield AgentEvent(
            type=EventType.FINISH,
            turn=0,
            finish_reason="error",
            content="Empty user input",
        )
        return

    perm_ctx = permission_context or PermissionContext()
    abort_sig = abort_signal or AbortSignal()

    if resume_messages:
        messages = list(resume_messages)
        messages.append(HumanMessage(content=user_input))
    else:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input),
        ]
        if transcript_writer:
            transcript_writer.write_message(messages[0])

    if transcript_writer:
        transcript_writer.write_message(messages[-1])

    openai_tools = _tools_to_openai_dicts(tools) if tools else None
    active_model = model

    turn = 0
    while turn < max_turns:
        turn += 1
        reactive_compact_attempted = False
        recovery_count = 0

        if abort_sig.is_triggered():
            yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="interrupted")
            return

        # ---- Pre-compact: MicroCompact every turn ----
        stats_parts: list[str] = []
        messages, micro_tokens_freed = apply_micro_compact(messages)
        if micro_tokens_freed > 0:
            stats_parts.append(f"micro ~{micro_tokens_freed} tok")

        messages, snip_stats = snip_compact(messages)
        if snip_stats.active:
            stats_parts.append(f"snip {snip_stats.details} ~{snip_stats.tokens_freed} tok")

        messages, collapse_stats = collapse_old_turns(messages)
        if collapse_stats.active:
            stats_parts.append(f"collapse {collapse_stats.details}")

        if stats_parts:
            yield AgentEvent(
                type=EventType.ERROR,
                turn=turn,
                content="compact: " + " | ".join(stats_parts),
                phase="compacting",
                status="compact",
            )

        # ---- Pre-compact: AutoCompact if near token limit ----
        if should_auto_compact(messages):
            logger.info("Auto-compacting: turn %d", turn)
            messages = await compact_conversation(messages, model)
            yield AgentEvent(
                type=EventType.ERROR,
                turn=turn,
                content="Conversation compacted (auto)",
                phase="compacting",
                status="compact",
            )

        # ---- Phase 1: Streaming LLM call ----
        while True:
            accumulated = None
            try:
                async with asyncio.timeout(llm_timeout_seconds):
                    if openai_tools:
                        stream = active_model.astream(messages, tools=openai_tools)  # type: ignore[arg-type]
                    else:
                        stream = active_model.astream(messages)
                    async for chunk in stream:
                        if abort_sig.is_triggered():
                            yield AgentEvent(
                                type=EventType.FINISH,
                                turn=turn,
                                finish_reason="interrupted",
                            )
                            return

                        if accumulated is None:
                            accumulated = chunk
                        else:
                            accumulated += chunk

                        content = chunk.content
                        if content and isinstance(content, str):
                            yield AgentEvent(
                                type=EventType.TEXT,
                                turn=turn,
                                content=content,
                                phase="thinking",
                            )

                        reasoning = _get_reasoning(chunk)
                        if reasoning:
                            yield AgentEvent(
                                type=EventType.REASONING,
                                turn=turn,
                                content=reasoning,
                                phase="thinking",
                            )
            except TimeoutError:
                logger.exception("LLM stream timed out at turn %d", turn)
                yield AgentEvent(
                    type=EventType.ERROR,
                    turn=turn,
                    content=f"LLM stream timed out after {llm_timeout_seconds:.1f}s",
                    status="generic_error",
                )
                yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
                return

            except Exception as e:
                if (
                    is_model_overloaded_error(e)
                    and fallback_model is not None
                    and active_model is not fallback_model
                ):
                    logger.warning("Model overloaded at turn %d, switching to fallback model", turn)
                    active_model = fallback_model
                    yield AgentEvent(
                        type=EventType.ERROR,
                        turn=turn,
                        content="Switched to fallback model due to high demand",
                        phase="fallback",
                        status="fallback",
                    )
                    continue

                if not is_context_overflow_error(e):
                    logger.exception("LLM call failed at turn %d", turn)
                    yield AgentEvent(
                        type=EventType.ERROR,
                        turn=turn,
                        content=str(e),
                        status="generic_error",
                    )
                    yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
                    return

                if reactive_compact_attempted:
                    logger.exception("Reactive compact retry still overflowed at turn %d", turn)
                    yield AgentEvent(
                        type=EventType.ERROR,
                        turn=turn,
                        content=str(e),
                        status="generic_error",
                    )
                    yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
                    return

                compacted = await try_reactive_compact(messages, model)
                if len(compacted) >= len(messages):
                    logger.exception(
                        "Reactive compact did not reduce message count at turn %d", turn
                    )
                    yield AgentEvent(
                        type=EventType.ERROR,
                        turn=turn,
                        content=str(e),
                        status="generic_error",
                    )
                    yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
                    return

                reactive_compact_attempted = True
                messages = compacted
                yield AgentEvent(
                    type=EventType.ERROR,
                    turn=turn,
                    content="Conversation compacted (reactive) — retrying",
                    phase="compacting",
                    status="compact",
                )
                continue

            finish_reason = get_finish_reason(accumulated)
            if finish_reason == "length" and accumulated is not None:
                truncated_content = (
                    accumulated.content
                    if isinstance(accumulated.content, str)
                    else str(accumulated.content or "")
                )
                truncated_tool_calls: list[dict[str, Any]] = []
                if accumulated.tool_calls:
                    for tc in accumulated.tool_calls:
                        truncated_tool_calls.append(_parse_tool_call(tc))

                messages.append(
                    AIMessage(
                        content=truncated_content,
                        tool_calls=truncated_tool_calls if truncated_tool_calls else [],
                    )
                )
                if transcript_writer:
                    transcript_writer.write_message(messages[-1])

                if recovery_count >= MAX_OUTPUT_RECOVERY:
                    yield AgentEvent(
                        type=EventType.ERROR,
                        turn=turn,
                        content="Output token limit hit too many times",
                        status="resume",
                    )
                    yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
                    return

                recovery_count += 1
                messages.append(HumanMessage(content=MAX_OUTPUT_RECOVERY_MSG))
                if transcript_writer:
                    transcript_writer.write_message(messages[-1])
                yield AgentEvent(
                    type=EventType.ERROR,
                    turn=turn,
                    content="max_output_tokens hit, resuming...",
                    phase="resuming",
                    status="resume",
                )
                continue

            break

        if accumulated is None:
            yield AgentEvent(
                type=EventType.ERROR,
                turn=turn,
                content="LLM returned no response",
                status="generic_error",
            )
            yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
            return

        # Build full AIMessage for message history
        response_content: str = (
            accumulated.content
            if isinstance(accumulated.content, str)
            else str(accumulated.content or "")
        )
        response_tool_calls: list[dict[str, Any]] = []
        if accumulated.tool_calls:
            for tc in accumulated.tool_calls:
                response_tool_calls.append(_parse_tool_call(tc))

        messages.append(
            AIMessage(
                content=response_content,
                tool_calls=response_tool_calls if response_tool_calls else [],  # type: ignore[arg-type]
            )
        )
        if transcript_writer:
            transcript_writer.write_message(messages[-1])

        # ---- Phase 2: No tool calls → done ----
        if not response_tool_calls:
            yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="completed")
            return

        # ---- Phase 3: Yield TOOL_CALL events ----
        for tc in response_tool_calls:  # type: ignore[assignment]
            yield AgentEvent(
                type=EventType.TOOL_CALL,
                turn=turn,
                tool_name=tc["name"],
                tool_call_id=str(tc["id"]),
                tool_args=tc["args"],
            )

        # ---- Phase 4: Execute tools (concurrent when safe) ----
        # Pre-check all tools: find tool, check permission
        exec_plan: list[dict[str, Any]] = []
        for tc in response_tool_calls:  # type: ignore[assignment]
            tool_name = tc["name"]
            tool_args = tc["args"]
            tc_id = str(tc["id"])

            if tool_name == "bash":
                command = str(tool_args.get("command", ""))
                if _should_redirect_bash_command(command):
                    messages.append(
                        ToolMessage(content=_ERROR_BASH_SEARCH_REDIRECT, tool_call_id=tc_id)
                    )
                    if transcript_writer:
                        transcript_writer.write_message(messages[-1])
                    yield AgentEvent(
                        type=EventType.ERROR,
                        turn=turn,
                        content=_ERROR_BASH_SEARCH_REDIRECT,
                        status="generic_error",
                        tool_name=tool_name,
                        tool_call_id=tc_id,
                        tool_args=tool_args,
                    )
                    continue

            tool = find_tool_by_name(tool_name, tools)
            if tool is None:
                error_content = _ERROR_TOOL_NOT_FOUND.format(name=tool_name)
                messages.append(ToolMessage(content=error_content, tool_call_id=tc_id))
                if transcript_writer:
                    transcript_writer.write_message(messages[-1])
                yield AgentEvent(
                    type=EventType.ERROR,
                    turn=turn,
                    content=error_content,
                    status="generic_error",
                    tool_name=tool_name,
                    tool_call_id=tc_id,
                    tool_args=tool_args,
                )
                continue

            is_concurrent = (
                tool.metadata.get("is_concurrency_safe", False)
                if isinstance(tool.metadata, dict)
                else False
            )
            decision = can_use_tool(
                tool_name=tool_name,
                tool_input=tool_args,
                tool_metadata=tool.metadata if isinstance(tool.metadata, dict) else {},
                context=perm_ctx,
            )
            if decision.behavior == PermissionBehavior.DENY:
                deny_msg = decision.message or _ERROR_PERMISSION_DENIED
                messages.append(ToolMessage(content=deny_msg, tool_call_id=tc_id))
                if transcript_writer:
                    transcript_writer.write_message(messages[-1])
                yield AgentEvent(
                    type=EventType.ERROR,
                    turn=turn,
                    content=deny_msg,
                    status="permission_denied",
                    tool_name=tool_name,
                    tool_call_id=tc_id,
                    tool_args=tool_args,
                )
                continue

            exec_plan.append({
                "name": tool_name,
                "args": tool_args,
                "id": tc_id,
                "is_concurrent": is_concurrent,
            })

        if abort_sig.is_triggered():
            for item in exec_plan:
                error_content = _ERROR_TOOL_ABORTED.format(reason="user_interrupted")
                messages.append(ToolMessage(content=error_content, tool_call_id=item["id"]))
                if transcript_writer:
                    transcript_writer.write_message(messages[-1])
                yield AgentEvent(
                    type=EventType.ERROR,
                    turn=turn,
                    content=error_content,
                    status="generic_error",
                    tool_name=item["name"],
                    tool_call_id=item["id"],
                    tool_args=item["args"],
                )
            yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="interrupted")
            return

        executor = StreamingToolExecutor(tools, turn)
        executor.add_all(exec_plan)

        for event in executor.get_completed_results():
            messages.append(ToolMessage(content=event.content, tool_call_id=event.tool_call_id))
            if transcript_writer:
                transcript_writer.write_message(messages[-1])
            yield event

        remaining_task = asyncio.create_task(executor.get_remaining_results())
        while not remaining_task.done():
            if abort_sig.is_triggered():
                executor.abort(AbortReason.USER_INTERRUPTED)
                break
            await asyncio.sleep(0.01)

        for event in await remaining_task:
            messages.append(ToolMessage(content=event.content, tool_call_id=event.tool_call_id))
            if transcript_writer:
                transcript_writer.write_message(messages[-1])
            yield event

        if abort_sig.is_triggered():
            yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="interrupted")
            return

    # Max turns reached
    yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="max_turns")
