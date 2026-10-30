"""handling web request"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from voice_code.desktop.protocol import BridgeEvent
from voice_code.web_demo.protocol import (
    PermissionResolvePayload,
    WebDemoEvent,
    WebDemoRequest,
    WebDemoResponse,
    error_response,
)
from voice_code.web_demo.runtime import (
    TenantSession,
    TenantSessionManager,
    WebDemoSessionError,
    normalize_permission_mode,
)
from voice_code.web_demo.sandbox import FileTooLargeError, SandboxBoundaryError


class WebDemoSender(Protocol):
    """传输层发送抽象，两种传输各自实现，handler只依赖这个接口。"""

    async def send(self, message: str) -> None: ...

    async def emit(self, event: BridgeEvent | WebDemoEvent) -> None: ...

    @property
    def client_id(self) -> str: ...


def parse_request(raw_message: str | bytes) -> WebDemoRequest:
    if isinstance(raw_message, bytes):
        raw_message = raw_message.decode("utf-8")
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise ValueError("Request must be valid JSON.") from exc
    try:
        return WebDemoRequest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def json_response(response: WebDemoResponse) -> str:
    return response.model_dump_json(by_alias=True, exclude_none=True)


def event_json(event: BridgeEvent | WebDemoEvent) -> str:
    return event.model_dump_json(by_alias=True, exclude_none=True)


def sanitize_bridge_event_for_web_demo(
    event: BridgeEvent | WebDemoEvent,
    *,
    sandbox_path: Path,
) -> BridgeEvent | WebDemoEvent:
    payload = _sanitize_sandbox_value(event.payload, sandbox_path=sandbox_path)
    payload = _strip_runtime_noise_from_payload(payload)
    if payload == event.payload:
        return event
    return event.model_copy(update={"payload": payload})


def _strip_runtime_noise_from_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    tool_name = str(payload.get("toolName", ""))
    if tool_name != "glob":
        return payload
    updated = dict(payload)
    for key in ("toolResult", "resultPreview"):
        value = updated.get(key)
        if isinstance(value, str):
            updated[key] = _strip_runtime_noise_lines(value)
    return updated


def _sanitize_sandbox_value(value: object, *, sandbox_path: Path) -> object:
    sandbox_text = str(sandbox_path)
    if not sandbox_text:
        return value
    if isinstance(value, str):
        return _replace_sandbox_text(value, sandbox_text)
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_sandbox_value(item, sandbox_path=sandbox_path)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_sandbox_value(item, sandbox_path=sandbox_path) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_sandbox_value(item, sandbox_path=sandbox_path) for item in value)
    return value


class _StreamingTextSanitizer:
    """按会话缓存的流式文本脱敏器。

    text/reasoning delta 是逐字流出的，沙盒绝对路径可能被拆到多个事件里，
    单事件 replace 永远匹配不到。这里把每个频道尾部最多 len(sandbox_text)
    个字符压住不放，等后续块凑齐（或回合结束 flush）再做替换。
    """

    _CHANNELS = {"agent.text.delta": "text", "agent.reasoning.delta": "reasoning"}

    def __init__(self, sandbox_text: str) -> None:
        self._sandbox_text = sandbox_text
        self._pending: dict[str, str] = {}

    @property
    def channels(self) -> dict[str, str]:
        return dict(self._CHANNELS)

    def sanitize_chunk(self, channel: str, chunk: str) -> str:
        buf = self._pending.get(channel, "") + chunk
        # 只压住"可能是路径开头"的尾部：buf 的最长后缀，同时是 sandbox_text 的真前缀
        sandbox = self._sandbox_text
        hold = 0
        for k in range(min(len(sandbox) - 1, len(buf)), 0, -1):
            if buf.endswith(sandbox[:k]):
                hold = k
                break
        emit_part = buf[: len(buf) - hold] if hold else buf
        self._pending[channel] = buf[len(buf) - hold :] if hold else ""
        return _replace_sandbox_text(emit_part, sandbox)

    def flush(self, channel: str) -> str:
        buf = self._pending.pop(channel, "")
        return _replace_sandbox_text(buf, self._sandbox_text)


def _replace_sandbox_text(text: str, sandbox_text: str) -> str:
    if not text:
        return text
    return text.replace(sandbox_text, ".").replace(".\\", "./")


def _strip_runtime_noise_lines(text: str) -> str:
    lines = text.splitlines()
    filtered = [
        line
        for line in lines
        if not line.startswith(".git/")
        and not line.startswith(".reasoning/")
        and not line.startswith("__pycache__/")
        and line not in {".git/", ".reasoning/", "__pycache__/"}
    ]
    return "\n".join(filtered)


def allowed_origins(host: str, configured: list[str] | None = None) -> list[str | None]:
    defaults = [
        None,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        f"http://{host}:5173",
        f"http://{host}:5174",
    ]
    extra = configured or origins_from_env()
    return defaults + [origin for origin in extra if origin not in defaults]


def origins_from_env() -> list[str]:
    raw = os.getenv("REASONING_WEB_DEMO_ALLOWED_ORIGINS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def diff_event(
    session: TenantSession,
    session_manager: TenantSessionManager,
) -> WebDemoEvent:
    diff = await session_manager.diff(session)
    return WebDemoEvent(
        event="sandbox.diff.updated",
        sessionId=session.conversation_id,
        payload=diff.to_payload(),
    )


def attach_session(
    session: TenantSession,
    sender: WebDemoSender,
    attached_sessions: set[TenantSession],
) -> None:
    # 同一连接重复 attach（每个请求都会调一次）时保留既有 emit，
    # 否则流式脱敏器的缓冲会被重置，跨块的绝对路径就漏了。
    if session in attached_sessions:
        return
    text_sanitizer = _StreamingTextSanitizer(str(session.sandbox.path))
    text_channels = text_sanitizer.channels

    async def emit(event: BridgeEvent | WebDemoEvent) -> None:
        channel = text_channels.get(event.event)
        if channel is not None and isinstance(event.payload.get("content"), str):
            chunk = text_sanitizer.sanitize_chunk(channel, str(event.payload["content"]))
            if not chunk:
                return
            event = event.model_copy(update={"payload": {**event.payload, "content": chunk}})
        elif event.event in ("agent.turn.finish", "agent.error"):
            # 回合结束前把压住的尾部文本补发出去
            for delta_event, pending_channel in text_channels.items():
                rest = text_sanitizer.flush(pending_channel)
                if rest:
                    await sender.emit(
                        event.model_copy(
                            update={
                                "event": delta_event,
                                "payload": {"content": rest},
                            }
                        )
                    )
        sanitized = sanitize_bridge_event_for_web_demo(
            event,
            sandbox_path=session.sandbox.path,
        )
        await sender.emit(sanitized)

    session.set_emit(emit)
    attached_sessions.add(session)


async def handle_request(
    session_manager: TenantSessionManager,
    request: WebDemoRequest,
    sender: WebDemoSender,
    attached_sessions: set[TenantSession],
) -> WebDemoResponse:
    if request.method == "tenant.session.attach":
        # 握手消息：session_token 字段承载 tenantToken（WS 无法自定义头）
        session = await session_manager.attach(
            request.session_token,
            client_id=sender.client_id,
        )
        attach_session(session, sender, attached_sessions)
        return WebDemoResponse(id=request.id, ok=True, payload=session.to_payload())

    session = await session_manager.get_session(request.session_token)
    attach_session(session, sender, attached_sessions)

    if request.method == "turn.start":
        return await handle_turn_start(session_manager, request, session)

    if request.method == "turn.interrupt":
        turn_id = optional_int(request.payload.get("turnId"))
        interrupted = bool(session.bridge and session.bridge.interrupt_turn(turn_id=turn_id))
        return WebDemoResponse(
            id=request.id,
            ok=True,
            payload={"interrupted": interrupted},
        )

    if request.method == "permission.resolve":
        if session.permission_approver is None:
            return error_response(
                "UNAVAILABLE",
                "No web permission approver is configured.",
                request_id=request.id,
            )
        try:
            payload = PermissionResolvePayload.model_validate(request.payload)
        except ValidationError as exc:
            return error_response("VALIDATION_ERROR", str(exc), request_id=request.id)
        resolved = session.permission_approver.resolve(payload)
        return WebDemoResponse(id=request.id, ok=True, payload={"resolved": resolved})

    if request.method == "sandbox.diff.get":
        diff = await session_manager.diff(session)
        return WebDemoResponse(id=request.id, ok=True, payload=diff.to_payload())

    if request.method == "sandbox.file.get":
        relative_path = str(request.payload.get("path", "")).strip()
        if not relative_path:
            return error_response(
                "VALIDATION_ERROR",
                "sandbox.file.get requires payload.path.",
                request_id=request.id,
            )
        try:
            sandbox_file = await session_manager.read_file(session, relative_path)
            session_manager.ensure_download_allowed(sandbox_file)
        except FileTooLargeError:
            return error_response(
                "FILE_TOO_LARGE",
                "Requested sandbox file exceeds the download size limit.",
                request_id=request.id,
            )
        except (FileNotFoundError, ValueError):
            return error_response(
                "NOT_FOUND",
                "Requested sandbox file was not found.",
                request_id=request.id,
            )
        return WebDemoResponse(id=request.id, ok=True, payload=sandbox_file.to_payload())

    if request.method == "sandbox.reset":
        await session_manager.reset(session)
        event = await diff_event(session, session_manager)
        await session.emit_event(event)
        return WebDemoResponse(id=request.id, ok=True, payload={"reset": True})

    if request.method == "session.discard":
        # 归档当前会话（transcript 保留）并开新会话；前端用新 payload 继续
        attached_sessions.discard(session)
        new_session = await session_manager.discard(session)
        attach_session(new_session, sender, attached_sessions)
        return WebDemoResponse(
            id=request.id,
            ok=True,
            payload={"discarded": True, **new_session.to_payload()},
        )

    return error_response(
        "UNKNOWN_METHOD",
        f"Unknown method: {request.method}",
        request_id=request.id,
    )


async def handle_turn_start(
    session_manager: TenantSessionManager,
    request: WebDemoRequest,
    session: TenantSession,
) -> WebDemoResponse:
    if session.bridge is None:
        return error_response(
            "UNAVAILABLE",
            "Demo session runtime is not ready.",
            request_id=request.id,
        )
    text = str(request.payload.get("text", "")).strip()
    if not text:
        return error_response(
            "VALIDATION_ERROR",
            "turn.start requires payload.text.",
            request_id=request.id,
        )
    permission_mode = normalize_permission_mode(
        str(request.payload.get("permissionMode", "")).strip()
    )
    session.permission_mode = permission_mode

    user_images: list[dict[str, str]] = []
    raw_images = request.payload.get("images") or []
    if not isinstance(raw_images, list):
        return error_response(
            "VALIDATION_ERROR", "turn.start images must be a list.", request_id=request.id
        )
    if len(raw_images) > 4:
        return error_response(
            "VALIDATION_ERROR", "turn.start accepts at most 4 images.", request_id=request.id
        )
    for item in raw_images:
        if not isinstance(item, dict):
            return error_response(
                "VALIDATION_ERROR", "turn.start images must be objects.", request_id=request.id
            )
        rel_path = str(item.get("path", "")).strip()
        if rel_path.startswith("./"):
            rel_path = rel_path[2:]
        if not rel_path:
            return error_response(
                "VALIDATION_ERROR", "turn.start image path is empty.", request_id=request.id
            )
        try:
            sandbox_file = await session_manager.read_file(session, rel_path)
        except (FileNotFoundError, FileTooLargeError, SandboxBoundaryError):
            return error_response(
                "VALIDATION_ERROR",
                f"turn.start image not found or unreadable: {rel_path}",
                request_id=request.id,
            )
        mime_type = sandbox_file.mime_type or ""
        if sandbox_file.encoding != "base64" or not mime_type.startswith("image/"):
            return error_response(
                "VALIDATION_ERROR",
                f"turn.start image must be an image file: {rel_path}",
                request_id=request.id,
            )
        user_images.append({"mime": mime_type, "data_base64": sandbox_file.content})

    session_manager.ensure_workspace_within_limits(session)
    try:
        result = await session.bridge.start_turn(
            text, permission_mode=permission_mode, user_images=user_images or None
        )
    except Exception:
        return error_response("TURN_FAILED", "Agent turn failed.", request_id=request.id)

    title_before = session.title
    session_manager.maybe_set_title(session, text)
    if session.title != title_before:
        await session.emit_event(
            WebDemoEvent(
                event="session.updated",
                sessionId=session.conversation_id,
                payload=session.to_payload(),
            )
        )

    with suppress(WebDemoSessionError):
        session_manager.ensure_workspace_within_limits(session)
    await session.emit_event(await diff_event(session, session_manager))
    return WebDemoResponse(
        id=request.id,
        ok=True,
        payload={
            "sessionId": result.session_id,
            "turnId": result.turn_id,
            "finishReason": result.finish_reason,
        },
    )


async def respond_to_raw_message(
    session_manager,
    raw_message: str | bytes,
    sender: WebDemoSender,
    attached_sessions: set[TenantSession],
) -> None:
    """算出响应再发出去"""
    response = await handle_raw_message(session_manager, raw_message, sender, attached_sessions)
    await sender.send(json_response(response))


async def handle_raw_message(
    session_manager,
    raw_message: str | bytes,
    sender: WebDemoSender,
    attached_sessions: set[TenantSession],
) -> WebDemoResponse:
    """解析 + 分发 + 统一把已知异常翻译成error_response"""
    try:
        request = parse_request(raw_message)
    except ValueError as exc:
        return error_response("INVALID_REQUEST", str(exc))

    try:
        return await handle_request(session_manager, request, sender, attached_sessions)
    except WebDemoSessionError as exc:
        return error_response(exc.code, exc.message, request_id=request.id)


async def release_attachments(attached_sessions: set[TenantSession]) -> None:
    """连接断开时：摘掉发射器 + 拒绝所有挂起的权限请求"""
    for session in attached_sessions:
        session.set_emit(None)
        if session.permission_approver is not None:
            session.permission_approver.deny_all_pending("UI disconnected.")


async def cancel_pending_tasks(tasks: set[asyncio.Task[None]]) -> None:
    """取消所有还在跑的消息处理任务并等它们收尾"""
    for task in tasks:
        task.cancel()
    if tasks:
        with suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)
