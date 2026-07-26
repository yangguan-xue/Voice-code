"""Agent 消息循环 — 手写 while 循环，astream 流式调用 LLM。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from voice_code.agent.abort import AbortSignal
from voice_code.agent.error_recovery import (
    MAX_OUTPUT_RECOVERY,
    MAX_OUTPUT_RECOVERY_MSG,
    get_finish_reason,
    is_model_overloaded_error,
)
from voice_code.agent.pipeline import run_precompact_phase
from voice_code.agent.runtime_state import AgentRuntimeState
from voice_code.agent.sinks import TranscriptSink
from voice_code.agent.types import AgentEvent, EventType
from voice_code.compact import (
    is_context_overflow_error,
    try_reactive_compact,
)
from voice_code.permissions import (
    PermissionBehavior,
    PermissionContext,
    can_use_tool,
)
from voice_code.session.transcript import TranscriptWriter
from voice_code.subagents.service import (
    RuntimeInvocationContext,
    activate_runtime_context,
    get_or_create_service,
)
from voice_code.telemetry import (
    ErrorCode,
    EventName,
    MetricName,
    bind_telemetry_context,
    new_correlation_id,
    new_telemetry_id,
    record_counter,
    record_histogram,
    start_span,
)
from voice_code.tools import find_tool_by_name
from voice_code.tools.streaming_executor import AbortReason, StreamingToolExecutor

if TYPE_CHECKING:
    from voice_code.memory.rag_service import MemoryRagService

logger = logging.getLogger(__name__)

_ERROR_TOOL_NOT_FOUND = (
    "<tool_use_error>Error: No such tool available: {name}</tool_use_error>"
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


def _bash_search_redirect_allowed(tool: BaseTool | None) -> bool:
    metadata = tool.metadata if tool and isinstance(tool.metadata, dict) else {}
    return bool(metadata.get("allow_discovery_commands", False))


def _get_reasoning(chunk: object) -> str:
    """Extract DeepSeek reasoning_content from a streaming chunk."""
    # LangChain ChatOpenAI stores extra fields in additional_kwargs
    additional = getattr(chunk, "additional_kwargs", None) or {}
    if isinstance(additional, dict):
        reasoning = additional.get("reasoning_content", "")
        if reasoning:
            return str(reasoning)
    return ""


def _record_llm_tokens(message: object, *, provider: str, model: str) -> None:
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        return
    for key, direction in (("input_tokens", "input"), ("output_tokens", "output")):
        value = usage.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            continue
        record_counter(
            MetricName.LLM_TOKENS_TOTAL,
            float(value),
            attributes={"provider": provider, "model": model, "direction": direction},
        )


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
    runtime_session_id: str | None = None,
    memory_messages: list[HumanMessage] | None = None,
    memory_service: MemoryRagService | None = None,
    memory_user_id: str = "local",
    memory_project_key: str | None = None,
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
    event_loop = asyncio.get_running_loop()
    session_id = runtime_session_id or (
        transcript_writer.file_path.stem
        if transcript_writer is not None
        else "ephemeral-session"
    )
    service = get_or_create_service(session_id, event_loop=event_loop)
    telemetry_fields = {
        "correlation_id": new_correlation_id(),
        "session_id": session_id,
        "turn_id": new_telemetry_id("turn"),
        "agent_id": "main",
        "request_id": new_telemetry_id("request"),
    }
    turn_started_at = time.monotonic()
    turn_outcome = "error"
    runtime_context = RuntimeInvocationContext(
        session_id=session_id,
        system_prompt=system_prompt,
        model=model,
        fallback_model=fallback_model,
        permission_context=perm_ctx,
        tools=tools,
        event_loop=event_loop,
        service=service,
    )
    pending_notifications = service.drain_notifications_as_messages()
    memory_msgs = list(memory_messages or [])
    if memory_service is not None:
        with bind_telemetry_context(**telemetry_fields):
            try:
                memory_msgs.extend(
                    await memory_service.get_memory_messages(
                        user_input,
                        user_id=memory_user_id,
                        project_key=memory_project_key,
                    )
                )
            except Exception:
                logger.warning(
                    "memory retrieval degraded",
                    extra={
                        "event": EventName.MEMORY_RETRIEVAL_DEGRADED,
                        "backend": "runtime",
                        "error_code": ErrorCode.MEMORY_INDEX_DEGRADED,
                    },
                )
    transcript_sink = TranscriptSink(transcript_writer)

    if resume_messages:
        messages = list(resume_messages)
        messages.extend(memory_msgs)
        messages.extend(pending_notifications)
        messages.append(HumanMessage(content=user_input))
    else:
        messages = [SystemMessage(content=system_prompt)]
        messages.extend(memory_msgs)
        messages.extend(pending_notifications)
        messages.append(HumanMessage(content=user_input))
        transcript_sink.write(messages[0])

    # Retrieved memories are derived, untrusted context. Persisting them would
    # duplicate private data into every transcript and replay stale injections.
    transcript_sink.write_many(pending_notifications)
    transcript_sink.write(messages[-1])

    openai_tools = _tools_to_openai_dicts(tools) if tools else None
    state = AgentRuntimeState(messages=messages, active_model=model)
    runtime_scope = activate_runtime_context(runtime_context)
    runtime_scope.__enter__()
    telemetry_scope = bind_telemetry_context(**telemetry_fields)
    telemetry_scope.__enter__()
    logger.info(
        "agent turn started",
        extra={
            "event": EventName.AGENT_TURN_STARTED,
            "outcome": "started",
            "mode": perm_ctx.mode,
        },
    )
    try:
        while state.turn < max_turns:
            turn = state.begin_turn()
            messages = state.messages
            active_model = state.active_model

            if abort_sig.is_triggered():
                yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="interrupted")
                return

            precompact = await run_precompact_phase(messages, model=model, turn=turn)
            messages = precompact.messages
            state.messages = messages
            for compact_event in precompact.events:
                yield compact_event

            # ---- Phase 1: Streaming LLM call ----
            while True:
                accumulated = None
                llm_started_at = time.monotonic()
                model_name = str(getattr(active_model, "model_name", "unknown"))
                provider = type(active_model).__name__
                logger.info(
                    "LLM request started",
                    extra={
                        "event": EventName.LLM_REQUEST_STARTED,
                        "provider": provider,
                        "model": model_name,
                        "attempt": state.recovery_count + 1,
                    },
                )
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
                except TimeoutError as exc:
                    logger.error(
                        "LLM request timed out",
                        extra={
                            "event": EventName.LLM_REQUEST_FAILED,
                            "provider": provider,
                            "model": model_name,
                            "outcome": "timeout",
                            "error_code": ErrorCode.PROVIDER_TIMEOUT,
                            "exception_type": type(exc).__name__,
                            "duration_ms": (time.monotonic() - llm_started_at) * 1000,
                        },
                    )
                    yield AgentEvent(
                        type=EventType.ERROR,
                        turn=turn,
                        content=f"LLM stream timed out after {llm_timeout_seconds:.1f}s",
                        status="generic_error",
                        error_code=ErrorCode.PROVIDER_TIMEOUT,
                    )
                    yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
                    return

                except Exception as e:
                    if (
                        is_model_overloaded_error(e)
                        and fallback_model is not None
                        and active_model is not fallback_model
                    ):
                        logger.warning(
                            "LLM fallback selected",
                            extra={
                                "event": EventName.LLM_FALLBACK_SELECTED,
                                "provider": provider,
                                "model": model_name,
                                "backend": type(fallback_model).__name__,
                                "outcome": "fallback",
                                "error_code": ErrorCode.PROVIDER_UNAVAILABLE,
                                "duration_ms": (time.monotonic() - llm_started_at) * 1000,
                            },
                        )
                        active_model = fallback_model
                        state.active_model = active_model
                        yield AgentEvent(
                            type=EventType.ERROR,
                            turn=turn,
                            content="Switched to fallback model due to high demand",
                            phase="fallback",
                            status="fallback",
                        )
                        continue

                    if not is_context_overflow_error(e):
                        logger.error(
                            "LLM request failed",
                            extra={
                                "event": EventName.LLM_REQUEST_FAILED,
                                "provider": provider,
                                "model": model_name,
                                "outcome": "error",
                                "error_code": ErrorCode.PROVIDER_UNAVAILABLE,
                                "exception_type": type(e).__name__,
                                "duration_ms": (time.monotonic() - llm_started_at) * 1000,
                            },
                        )
                        yield AgentEvent(
                            type=EventType.ERROR,
                            turn=turn,
                            content="Model provider is unavailable. Please retry.",
                            status="generic_error",
                            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                        )
                        yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
                        return

                    if state.reactive_compact_attempted:
                        logger.error(
                            "LLM context recovery failed",
                            extra={
                                "event": EventName.LLM_REQUEST_FAILED,
                                "provider": provider,
                                "model": model_name,
                                "outcome": "context_overflow",
                                "error_code": ErrorCode.MODEL_RESPONSE_INVALID,
                                "exception_type": type(e).__name__,
                            },
                        )
                        yield AgentEvent(
                            type=EventType.ERROR,
                            turn=turn,
                            content="Model context recovery failed.",
                            status="generic_error",
                            error_code=ErrorCode.MODEL_RESPONSE_INVALID,
                        )
                        yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
                        return

                    logger.warning(
                        "LLM context overflow triggered recovery",
                        extra={
                            "event": EventName.LLM_REQUEST_FAILED,
                            "provider": provider,
                            "model": model_name,
                            "outcome": "context_overflow",
                            "error_code": ErrorCode.MODEL_RESPONSE_INVALID,
                            "duration_ms": (time.monotonic() - llm_started_at) * 1000,
                        },
                    )
                    compact_started_at = time.monotonic()
                    compact_outcome = "error"
                    try:
                        with start_span("compact.run", {"strategy": "reactive"}):
                            compacted = await try_reactive_compact(messages, model)
                        compact_outcome = "success"
                    finally:
                        record_counter(
                            MetricName.COMPACTION_TOTAL,
                            attributes={
                                "strategy": "reactive",
                                "outcome": compact_outcome,
                            },
                        )
                        record_histogram(
                            MetricName.COMPACTION_DURATION_SECONDS,
                            time.monotonic() - compact_started_at,
                            attributes={"strategy": "reactive"},
                        )
                    if len(compacted) >= len(messages):
                        logger.error(
                            "LLM context recovery made no progress",
                            extra={
                                "event": EventName.LLM_REQUEST_FAILED,
                                "provider": provider,
                                "model": model_name,
                                "outcome": "context_overflow",
                                "error_code": ErrorCode.MODEL_RESPONSE_INVALID,
                                "exception_type": type(e).__name__,
                            },
                        )
                        yield AgentEvent(
                            type=EventType.ERROR,
                            turn=turn,
                            content="Model context recovery made no progress.",
                            status="generic_error",
                            error_code=ErrorCode.MODEL_RESPONSE_INVALID,
                        )
                        yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
                        return

                    state.reactive_compact_attempted = True
                    messages = compacted
                    state.messages = messages
                    yield AgentEvent(
                        type=EventType.ERROR,
                        turn=turn,
                        content="Conversation compacted (reactive) — retrying",
                        phase="compacting",
                        status="compact",
                    )
                    continue

                logger.info(
                    "LLM request finished",
                    extra={
                        "event": EventName.LLM_REQUEST_FINISHED,
                        "provider": provider,
                        "model": model_name,
                        "outcome": "success",
                        "duration_ms": (time.monotonic() - llm_started_at) * 1000,
                    },
                )
                _record_llm_tokens(accumulated, provider=provider, model=model_name)
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
                    transcript_sink.write(messages[-1])

                    if state.recovery_count >= MAX_OUTPUT_RECOVERY:
                        yield AgentEvent(
                            type=EventType.ERROR,
                            turn=turn,
                            content="Output token limit hit too many times",
                            status="resume",
                        )
                        yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="error")
                        return

                    state.recovery_count += 1
                    messages.append(HumanMessage(content=MAX_OUTPUT_RECOVERY_MSG))
                    transcript_sink.write(messages[-1])
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
            transcript_sink.write(messages[-1])

            # ---- Phase 2: No tool calls → done ----
            if not response_tool_calls:
                if memory_service is not None:
                    enqueue_completed = getattr(
                        memory_service,
                        "enqueue_completed_turn",
                        None,
                    )
                    if enqueue_completed is not None:
                        try:
                            enqueue_completed(
                                user_input=user_input,
                                assistant_response=response_content,
                                user_id=memory_user_id,
                                project_key=memory_project_key,
                                session_id=session_id,
                                turn_id=f"turn-{turn}-{uuid.uuid4().hex}",
                            )
                        except Exception:
                            logger.warning(
                                "memory extraction enqueue failed",
                                extra={
                                    "event": EventName.MEMORY_EXTRACTION_ENQUEUE_FAILED,
                                    "error_code": ErrorCode.BACKGROUND_TASK_FAILED,
                                },
                            )
                turn_outcome = "completed"
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
            exec_plan: list[dict[str, Any]] = []
            for tc in response_tool_calls:  # type: ignore[assignment]
                tool_name = tc["name"]
                tool_args = tc["args"]
                tc_id = str(tc["id"])
                tool = find_tool_by_name(tool_name, tools)

                if tool_name == "bash":
                    command = str(tool_args.get("command", ""))
                    should_block_redirect = (
                        _should_redirect_bash_command(command)
                        and not _bash_search_redirect_allowed(tool)
                    )
                    if should_block_redirect:
                        messages.append(
                            ToolMessage(content=_ERROR_BASH_SEARCH_REDIRECT, tool_call_id=tc_id)
                        )
                        transcript_sink.write(messages[-1])
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

                if tool is None:
                    error_content = _ERROR_TOOL_NOT_FOUND.format(name=tool_name)
                    messages.append(ToolMessage(content=error_content, tool_call_id=tc_id))
                    transcript_sink.write(messages[-1])
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
                with bind_telemetry_context(tool_call_id=tc_id):
                    decision = can_use_tool(
                        tool_name=tool_name,
                        tool_input=tool_args,
                        tool_metadata=tool.metadata if isinstance(tool.metadata, dict) else {},
                        context=perm_ctx,
                    )
                if decision.behavior == PermissionBehavior.DENY:
                    deny_msg = decision.message or _ERROR_PERMISSION_DENIED
                    messages.append(ToolMessage(content=deny_msg, tool_call_id=tc_id))
                    transcript_sink.write(messages[-1])
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
                    transcript_sink.write(messages[-1])
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
                transcript_sink.write(messages[-1])
                yield event

            remaining_task = asyncio.create_task(executor.get_remaining_results())
            while not remaining_task.done():
                if abort_sig.is_triggered():
                    executor.abort(AbortReason.USER_INTERRUPTED)
                    break
                await asyncio.sleep(0.01)

            for event in await remaining_task:
                messages.append(ToolMessage(content=event.content, tool_call_id=event.tool_call_id))
                transcript_sink.write(messages[-1])
                yield event

            if abort_sig.is_triggered():
                yield AgentEvent(type=EventType.FINISH, turn=turn, finish_reason="interrupted")
                return

        # Max turns reached
        turn_outcome = "max_turns"
        yield AgentEvent(type=EventType.FINISH, turn=state.turn, finish_reason="max_turns")
    finally:
        runtime_scope.__exit__(None, None, None)
        logger.info(
            "agent turn finished",
            extra={
                "event": EventName.AGENT_TURN_FINISHED,
                "outcome": turn_outcome,
                "mode": perm_ctx.mode,
                "duration_ms": (time.monotonic() - turn_started_at) * 1000,
            },
        )
        telemetry_scope.__exit__(None, None, None)
