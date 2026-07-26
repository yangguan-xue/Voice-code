"""Loopback WebSocket transport for the desktop bridge."""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from voice_code.desktop.bridge import DesktopAgentBridge
from voice_code.desktop.permissions import DesktopPermissionApprover
from voice_code.desktop.protocol import (
    BridgeEvent,
    BridgeRequest,
    BridgeResponse,
    PermissionResolvePayload,
)
from voice_code.session.lifecycle import archive_session
from voice_code.session.manager import group_session_summaries, list_session_summaries


class DesktopRuntimeActions(Protocol):
    def bootstrap_context(self) -> dict[str, object]: ...

    def resume_session(self, session_id: str) -> dict[str, object]: ...

    def create_session(self) -> Any: ...

    def checkout_branch(self, branch: str) -> dict[str, object]: ...

    def select_model_profile(self, profile: str) -> Any: ...

    def configure_model(self, config: dict[str, str]) -> Any: ...


@dataclass(frozen=True, slots=True)
class RunningDesktopBridgeServer:
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"


class DesktopBridgeServer:
    """Serve the desktop bridge over a loopback WebSocket."""

    def __init__(
        self,
        *,
        bridge: DesktopAgentBridge,
        token: str,
        permission_approver: DesktopPermissionApprover | None = None,
        actions: DesktopRuntimeActions | None = None,
    ) -> None:
        if not token:
            raise ValueError("desktop bridge token must not be empty")
        self._bridge = bridge
        self._token = token
        self._permission_approver = permission_approver
        self._actions = actions

    @asynccontextmanager
    async def run(self, *, host: str = "127.0.0.1", port: int = 0):
        async with serve(self._handle_connection, host, port, origins=_allowed_origins(host)) as ws:
            socket = ws.sockets[0]
            bound_host, bound_port = socket.getsockname()[:2]
            yield RunningDesktopBridgeServer(host=str(bound_host), port=int(bound_port))

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        sender = _ConnectionSender(websocket)
        previous_emit = self._bridge.set_emit(sender.emit)
        tasks: set[asyncio.Task[None]] = set()
        try:
            async for raw_message in websocket:
                task = asyncio.create_task(self._respond_to_raw_message(raw_message, sender))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except ConnectionClosed:
            pass
        finally:
            self._bridge.set_emit(previous_emit)
            if self._permission_approver is not None:
                self._permission_approver.deny_all_pending("UI disconnected.")
            for task in tasks:
                task.cancel()
            if tasks:
                with suppress(Exception):
                    await asyncio.gather(*tasks, return_exceptions=True)

    async def _respond_to_raw_message(
        self,
        raw_message: str | bytes,
        sender: _ConnectionSender,
    ) -> None:
        response = await self._handle_raw_message(raw_message)
        await sender.send(_json_response(response))

    async def _handle_raw_message(
        self,
        raw_message: str | bytes,
    ) -> BridgeResponse:
        try:
            request = _parse_request(raw_message)
        except ValueError as exc:
            return _error_response("INVALID_REQUEST", str(exc))

        if request.token != self._token:
            return _error_response(
                "UNAUTHORIZED",
                "Invalid desktop bridge token.",
                request_id=request.id,
            )

        if request.method == "app.bootstrap":
            context = self._actions.bootstrap_context() if self._actions is not None else {}
            return BridgeResponse(
                id=request.id,
                ok=True,
                payload={
                    "sessionId": self._bridge.session_id,
                    "sessionGroups": _session_groups_payload(self._bridge.session_id),
                    **context,
                },
            )

        if request.method == "session.resume":
            session_id = str(request.payload.get("sessionId", "")).strip()
            if not session_id:
                return _error_response(
                    "VALIDATION_ERROR",
                    "session.resume requires payload.sessionId.",
                    request_id=request.id,
                )
            if self._actions is None:
                return _error_response(
                    "UNAVAILABLE",
                    "Session resume is unavailable.",
                    request_id=request.id,
                )
            try:
                payload = self._actions.resume_session(session_id)
                if inspect.isawaitable(payload):
                    payload = await payload
            except (FileNotFoundError, ValueError) as exc:
                return _error_response("SESSION_RESUME_FAILED", str(exc), request_id=request.id)
            except Exception:
                return _error_response(
                    "SESSION_RESUME_FAILED",
                    "Session resume failed.",
                    request_id=request.id,
                )
            return BridgeResponse(id=request.id, ok=True, payload=payload)

        if request.method == "session.create":
            if self._actions is None:
                return _error_response(
                    "UNAVAILABLE",
                    "Session creation is unavailable.",
                    request_id=request.id,
                )
            try:
                payload = self._actions.create_session()
                if inspect.isawaitable(payload):
                    payload = await payload
            except ValueError as exc:
                return _error_response("SESSION_CREATE_FAILED", str(exc), request_id=request.id)
            except Exception:
                return _error_response(
                    "SESSION_CREATE_FAILED",
                    "Session creation failed.",
                    request_id=request.id,
                )
            return BridgeResponse(id=request.id, ok=True, payload=payload)

        if request.method == "git.checkout":
            branch = str(request.payload.get("branch", "")).strip()
            if not branch:
                return _error_response(
                    "VALIDATION_ERROR",
                    "git.checkout requires payload.branch.",
                    request_id=request.id,
                )
            if self._actions is None:
                return _error_response(
                    "UNAVAILABLE",
                    "Git checkout is unavailable.",
                    request_id=request.id,
                )
            try:
                payload = self._actions.checkout_branch(branch)
                if inspect.isawaitable(payload):
                    payload = await payload
            except ValueError as exc:
                return _error_response("GIT_CHECKOUT_FAILED", str(exc), request_id=request.id)
            except Exception:
                return _error_response(
                    "GIT_CHECKOUT_FAILED",
                    "Git checkout failed.",
                    request_id=request.id,
                )
            return BridgeResponse(id=request.id, ok=True, payload=payload)

        if request.method == "model.select":
            profile = str(request.payload.get("profile", "")).strip()
            if not profile:
                return _error_response(
                    "VALIDATION_ERROR",
                    "model.select requires payload.profile.",
                    request_id=request.id,
                )
            if self._actions is None:
                return _error_response(
                    "UNAVAILABLE",
                    "Model selection is unavailable.",
                    request_id=request.id,
                )
            try:
                payload = self._actions.select_model_profile(profile)
                if inspect.isawaitable(payload):
                    payload = await payload
            except ValueError as exc:
                return _error_response("MODEL_SELECT_FAILED", str(exc), request_id=request.id)
            except Exception:
                return _error_response(
                    "MODEL_SELECT_FAILED",
                    "Model selection failed.",
                    request_id=request.id,
                )
            return BridgeResponse(id=request.id, ok=True, payload=payload)

        if request.method == "model.configure":
            config = {
                "id": str(request.payload.get("id", "")).strip(),
                "displayName": str(request.payload.get("displayName", "")).strip(),
                "baseUrl": str(request.payload.get("baseUrl", "")).strip(),
                "apiKey": str(request.payload.get("apiKey", "")).strip(),
                "modelName": str(request.payload.get("modelName", "")).strip(),
            }
            if not all(config.values()):
                return _error_response(
                    "VALIDATION_ERROR",
                    "model.configure requires id, displayName, baseUrl, apiKey and modelName.",
                    request_id=request.id,
                )
            if not config["baseUrl"].startswith(("http://", "https://")):
                return _error_response(
                    "VALIDATION_ERROR",
                    "model.configure requires an HTTP or HTTPS baseUrl.",
                    request_id=request.id,
                )
            if self._actions is None:
                return _error_response(
                    "UNAVAILABLE",
                    "Custom model configuration is unavailable.",
                    request_id=request.id,
                )
            try:
                payload = self._actions.configure_model(config)
                if inspect.isawaitable(payload):
                    payload = await payload
            except ValueError as exc:
                return _error_response("MODEL_CONFIGURE_FAILED", str(exc), request_id=request.id)
            except Exception:
                return _error_response(
                    "MODEL_CONFIGURE_FAILED",
                    "Custom model configuration failed.",
                    request_id=request.id,
                )
            return BridgeResponse(id=request.id, ok=True, payload=payload)

        if request.method == "turn.start":
            text = str(request.payload.get("text", "")).strip()
            permission_mode = str(request.payload.get("permissionMode", "")).strip()
            if not text:
                return _error_response(
                    "VALIDATION_ERROR",
                    "turn.start requires payload.text.",
                    request_id=request.id,
                )
            try:
                result = await self._bridge.start_turn(
                    text,
                    permission_mode=permission_mode or None,
                )
            except Exception:
                return _error_response(
                    "TURN_FAILED",
                    "Agent turn failed.",
                    request_id=request.id,
                )
            return BridgeResponse(
                id=request.id,
                ok=True,
                payload={
                    "sessionId": result.session_id,
                    "turnId": result.turn_id,
                    "finishReason": result.finish_reason,
                },
            )

        if request.method == "session.archive":
            session_id = str(request.payload.get("sessionId", "")).strip()
            if not session_id:
                return _error_response(
                    "VALIDATION_ERROR",
                    "session.archive requires payload.sessionId.",
                    request_id=request.id,
                )
            result = archive_session(session_id)
            return BridgeResponse(
                id=request.id,
                ok=True,
                payload={
                    "archived": result.archived,
                    "sessionGroups": _session_groups_payload(self._bridge.session_id),
                },
            )

        if request.method == "turn.interrupt":
            turn_id = _optional_int(request.payload.get("turnId"))
            interrupted = self._bridge.interrupt_turn(turn_id=turn_id)
            return BridgeResponse(
                id=request.id,
                ok=True,
                payload={"interrupted": interrupted},
            )

        if request.method == "permission.resolve":
            if self._permission_approver is None:
                return _error_response(
                    "UNAVAILABLE",
                    "No desktop permission approver is configured.",
                    request_id=request.id,
                )
            try:
                payload = PermissionResolvePayload.model_validate(request.payload)
            except ValidationError as exc:
                return _error_response("VALIDATION_ERROR", str(exc), request_id=request.id)
            resolved = self._permission_approver.resolve(payload)
            return BridgeResponse(
                id=request.id,
                ok=True,
                payload={"resolved": resolved},
            )

        return _error_response(
            "UNKNOWN_METHOD",
            f"Unknown method: {request.method}",
            request_id=request.id,
        )

    async def emit_to(self, websocket: ServerConnection, event: BridgeEvent) -> None:
        await websocket.send(event.model_dump_json(by_alias=True, exclude_none=True))


class _ConnectionSender:
    def __init__(self, websocket: ServerConnection) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()

    async def send(self, message: str) -> None:
        async with self._lock:
            await self._websocket.send(message)

    async def emit(self, event: BridgeEvent) -> None:
        await self.send(event.model_dump_json(by_alias=True, exclude_none=True))


def websocket_emitter(websocket: ServerConnection):
    async def emit(event: BridgeEvent) -> None:
        await websocket.send(event.model_dump_json(by_alias=True, exclude_none=True))

    return emit


def _parse_request(raw_message: str | bytes) -> BridgeRequest:
    if isinstance(raw_message, bytes):
        raw_message = raw_message.decode("utf-8")
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise ValueError("Request must be valid JSON.") from exc
    try:
        return BridgeRequest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def _json_response(response: BridgeResponse) -> str:
    return response.model_dump_json(by_alias=True, exclude_none=True)


def _error_response(code: str, message: str, *, request_id: str = "") -> BridgeResponse:
    return BridgeResponse(
        id=request_id,
        ok=False,
        error={"code": code, "message": message},
    )


def _allowed_origins(host: str) -> list[str | None]:
    return [
        None,
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        f"http://{host}:5173",
    ]


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _session_groups_payload(current_session_id: str) -> list[dict[str, object]]:
    groups = group_session_summaries(
        list_session_summaries(limit=40, current_session_id=current_session_id)
    )
    return [
        {
            "id": group.key,
            "folderName": group.label,
            "workspacePath": group.project_path,
            "sessions": [
                {
                    "id": session.id,
                    "title": session.title or "未命名任务",
                    "isActive": session.is_current,
                    "isEmpty": False,
                }
                for session in group.sessions
            ],
        }
        for group in groups
    ]
