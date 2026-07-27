"""WebSocket transport for the web demo sandbox agent."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from voice_code.desktop.protocol import BridgeEvent
from voice_code.web_demo.protocol import (
    PermissionResolvePayload,
    WebDemoEvent,
    WebDemoRequest,
    WebDemoResponse,
    error_response,
)
from voice_code.web_demo.runtime import (
    DemoSession,
    DemoSessionManager,
    WebDemoSessionError,
    normalize_permission_mode,
)


@dataclass(frozen=True, slots=True)
class RunningWebDemoServer:
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"


class WebDemoServer:
    """Serve invited browser demo sessions over WebSocket."""

    def __init__(
        self,
        *,
        session_manager: DemoSessionManager,
        allowed_origins: list[str] | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._allowed_origins = allowed_origins

    @asynccontextmanager
    async def run(self, *, host: str = "127.0.0.1", port: int = 8787):
        async with serve(
            self._handle_connection,
            host,
            port,
            origins=_allowed_origins(host, configured=self._allowed_origins),
        ) as ws:
            socket = ws.sockets[0]
            bound_host, bound_port = socket.getsockname()[:2]
            yield RunningWebDemoServer(host=str(bound_host), port=int(bound_port))

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        sender = _ConnectionSender(websocket)
        tasks: set[asyncio.Task[None]] = set()
        attached_sessions: set[DemoSession] = set()
        try:
            async for raw_message in websocket:
                task = asyncio.create_task(
                    self._respond_to_raw_message(raw_message, sender, attached_sessions)
                )
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except ConnectionClosed:
            pass
        finally:
            for session in attached_sessions:
                session.set_emit(None)
                if session.permission_approver is not None:
                    session.permission_approver.deny_all_pending("UI disconnected.")
            for task in tasks:
                task.cancel()
            if tasks:
                with suppress(Exception):
                    await asyncio.gather(*tasks, return_exceptions=True)

    async def _respond_to_raw_message(
        self,
        raw_message: str | bytes,
        sender: _ConnectionSender,
        attached_sessions: set[DemoSession],
    ) -> None:
        response = await self._handle_raw_message(raw_message, sender, attached_sessions)
        await sender.send(_json_response(response))

    async def _handle_raw_message(
        self,
        raw_message: str | bytes,
        sender: _ConnectionSender,
        attached_sessions: set[DemoSession],
    ) -> WebDemoResponse:
        try:
            request = _parse_request(raw_message)
        except ValueError as exc:
            return error_response("INVALID_REQUEST", str(exc))

        try:
            return await self._handle_request(request, sender, attached_sessions)
        except WebDemoSessionError as exc:
            return error_response(exc.code, exc.message, request_id=request.id)

    async def _handle_request(
        self,
        request: WebDemoRequest,
        sender: _ConnectionSender,
        attached_sessions: set[DemoSession],
    ) -> WebDemoResponse:
        if request.method == "demo.session.create":
            access_code = str(request.payload.get("accessCode", ""))
            session = await self._session_manager.create_session(
                access_code,
                client_id=_client_id(sender),
            )
            _attach_session(session, sender, attached_sessions)
            return WebDemoResponse(id=request.id, ok=True, payload=session.to_payload())

        session = await self._session_manager.get_by_token(request.session_token)
        _attach_session(session, sender, attached_sessions)

        if request.method == "turn.start":
            return await self._handle_turn_start(request, session)

        if request.method == "turn.interrupt":
            turn_id = _optional_int(request.payload.get("turnId"))
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
            diff = await self._session_manager.diff(session)
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
                sandbox_file = await self._session_manager.read_file(session, relative_path)
                self._session_manager.ensure_download_allowed(sandbox_file)
            except (FileNotFoundError, ValueError):
                return error_response(
                    "NOT_FOUND",
                    "Requested sandbox file was not found.",
                    request_id=request.id,
                )
            return WebDemoResponse(id=request.id, ok=True, payload=sandbox_file.to_payload())

        if request.method == "sandbox.reset":
            await self._session_manager.reset(session)
            event = await _diff_event(session, self._session_manager)
            await session.emit_event(event)
            return WebDemoResponse(id=request.id, ok=True, payload={"reset": True})

        if request.method == "session.discard":
            await self._session_manager.discard(session)
            return WebDemoResponse(id=request.id, ok=True, payload={"discarded": True})

        return error_response(
            "UNKNOWN_METHOD",
            f"Unknown method: {request.method}",
            request_id=request.id,
        )

    async def _handle_turn_start(
        self,
        request: WebDemoRequest,
        session: DemoSession,
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
        self._session_manager.ensure_workspace_within_limits(session)
        try:
            result = await session.bridge.start_turn(text, permission_mode=permission_mode)
        except Exception:
            return error_response("TURN_FAILED", "Agent turn failed.", request_id=request.id)

        with suppress(WebDemoSessionError):
            self._session_manager.ensure_workspace_within_limits(session)
        await session.emit_event(await _diff_event(session, self._session_manager))
        return WebDemoResponse(
            id=request.id,
            ok=True,
            payload={
                "sessionId": result.session_id,
                "turnId": result.turn_id,
                "finishReason": result.finish_reason,
            },
        )


class _ConnectionSender:
    def __init__(self, websocket: ServerConnection) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()

    async def send(self, message: str) -> None:
        async with self._lock:
            await self._websocket.send(message)

    async def emit(self, event: BridgeEvent | WebDemoEvent) -> None:
        await self.send(_event_json(event))

    @property
    def client_id(self) -> str:
        remote = getattr(self._websocket, "remote_address", None)
        if isinstance(remote, tuple) and remote:
            return str(remote[0] or "anonymous")
        return "anonymous"


def _attach_session(
    session: DemoSession,
    sender: _ConnectionSender,
    attached_sessions: set[DemoSession],
) -> None:
    async def emit(event: BridgeEvent | WebDemoEvent) -> None:
        sanitized = _sanitize_bridge_event_for_web_demo(
            event,
            sandbox_path=session.sandbox.path,
        )
        await sender.emit(sanitized)

    session.set_emit(emit)
    attached_sessions.add(session)


async def _diff_event(
    session: DemoSession,
    session_manager: DemoSessionManager,
) -> WebDemoEvent:
    diff = await session_manager.diff(session)
    return WebDemoEvent(
        event="sandbox.diff.updated",
        session_id=session.session_id,
        payload=diff.to_payload(),
    )


def _parse_request(raw_message: str | bytes) -> WebDemoRequest:
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


def _json_response(response: WebDemoResponse) -> str:
    return response.model_dump_json(by_alias=True, exclude_none=True)


def _event_json(event: BridgeEvent | WebDemoEvent) -> str:
    return event.model_dump_json(by_alias=True, exclude_none=True)


def _sanitize_bridge_event_for_web_demo(
    event: BridgeEvent | WebDemoEvent,
    *,
    sandbox_path: Path,
) -> BridgeEvent | WebDemoEvent:
    payload = _sanitize_sandbox_value(event.payload, sandbox_path=sandbox_path)
    payload = _strip_runtime_noise_from_payload(payload)
    if payload == event.payload:
        return event
    return event.model_copy(update={"payload": payload})


def _sanitize_sandbox_value(value: object, *, sandbox_path: Path) -> object:
    sandbox_text = str(sandbox_path)
    if not sandbox_text:
        return value
    if isinstance(value, str):
        return value.replace(sandbox_text, ".").replace(".\\", "./")
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


def _allowed_origins(host: str, configured: list[str] | None = None) -> list[str | None]:
    defaults = [
        None,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        f"http://{host}:5173",
        f"http://{host}:5174",
    ]
    extra = configured or _origins_from_env()
    return defaults + [origin for origin in extra if origin not in defaults]


def _client_id(sender: _ConnectionSender) -> str:
    return sender.client_id


def _origins_from_env() -> list[str]:
    raw = os.getenv("REASONING_WEB_DEMO_ALLOWED_ORIGINS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
