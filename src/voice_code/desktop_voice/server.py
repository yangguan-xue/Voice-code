"""Loopback WebSocket server for the desktop voice bridge."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from voice_code.desktop.permissions import DesktopPermissionApprover
from voice_code.desktop.protocol import BridgeEvent, PermissionResolvePayload
from voice_code.desktop_voice.bridge import DesktopVoiceBridge
from voice_code.desktop_voice.protocol import (
    VoiceBridgeEvent,
    VoiceBridgeRequest,
    VoiceBridgeResponse,
    VoiceLaunchMode,
)


@dataclass(frozen=True, slots=True)
class RunningDesktopVoiceServer:
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"


class DesktopVoiceServer:
    def __init__(
        self,
        *,
        bridge: DesktopVoiceBridge,
        token: str,
        permission_approver: DesktopPermissionApprover | None = None,
    ) -> None:
        if not token:
            raise ValueError("desktop voice bridge token must not be empty")
        self._bridge = bridge
        self._token = token
        self._permission_approver = permission_approver

    @asynccontextmanager
    async def run(self, *, host: str = "127.0.0.1", port: int = 0):
        async with serve(self._handle_connection, host, port, origins=_allowed_origins(host)) as ws:
            socket = ws.sockets[0]
            bound_host, bound_port = socket.getsockname()[:2]
            yield RunningDesktopVoiceServer(host=str(bound_host), port=int(bound_port))

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
        await sender.send(response.model_dump_json(by_alias=True, exclude_none=True))

    async def _handle_raw_message(self, raw_message: str | bytes) -> VoiceBridgeResponse:
        try:
            request = _parse_request(raw_message)
        except ValueError as exc:
            return _error_response("INVALID_REQUEST", str(exc))

        if request.token != self._token:
            return _error_response(
                "UNAUTHORIZED",
                "Invalid desktop voice bridge token.",
                request_id=request.id,
            )

        if request.method == "voice.bootstrap":
            return VoiceBridgeResponse(
                id=request.id,
                ok=True,
                payload={
                    "sessionId": self._bridge.session_id,
                    "ttsMuted": self._bridge.tts_muted,
                },
            )

        if request.method == "voice.turn.start":
            text = str(request.payload.get("text", "")).strip()
            if not text:
                return _error_response(
                    "VALIDATION_ERROR",
                    "voice.turn.start requires payload.text.",
                    request_id=request.id,
                )
            mode = _mode_from_payload(request.payload)
            context_version = _int_from_payload(request.payload, "contextVersion")
            worktree_task_id = str(request.payload.get("worktreeTaskId", "")).strip()
            context_messages = _context_messages_from_payload(request.payload, mode=mode)
            result = await self._bridge.start_turn(
                text,
                context_messages=context_messages,
                mode=mode,
                context_version=context_version,
                worktree_task_id=worktree_task_id,
            )
            return VoiceBridgeResponse(
                id=request.id,
                ok=True,
                payload={
                    "sessionId": result.session_id,
                    "turnId": result.turn_id,
                    "finishReason": result.finish_reason,
                    "mode": result.mode,
                    "contextVersion": result.context_version,
                    "worktreeTaskId": result.worktree_task_id,
                    "worktreePath": result.worktree_path,
                },
            )

        if request.method == "voice.turn.interrupt":
            interrupted = self._bridge.interrupt_turn()
            return VoiceBridgeResponse(
                id=request.id,
                ok=True,
                payload={"interrupted": interrupted},
            )

        if request.method == "voice.tts.setMuted":
            muted = bool(request.payload.get("muted"))
            self._bridge.set_tts_muted(muted)
            return VoiceBridgeResponse(
                id=request.id,
                ok=True,
                payload={"ttsMuted": self._bridge.tts_muted},
            )

        if request.method == "permission.resolve":
            if self._permission_approver is None:
                return _error_response(
                    "UNAVAILABLE",
                    "No desktop voice permission approver is configured.",
                    request_id=request.id,
                )
            try:
                payload = PermissionResolvePayload.model_validate(request.payload)
            except ValidationError as exc:
                return _error_response("VALIDATION_ERROR", str(exc), request_id=request.id)
            resolved = self._permission_approver.resolve(payload)
            return VoiceBridgeResponse(
                id=request.id,
                ok=True,
                payload={"resolved": resolved},
            )

        if request.method == "voice.transcribe":
            audio_base64 = str(request.payload.get("audioBase64", ""))
            if not audio_base64:
                return _error_response(
                    "VALIDATION_ERROR",
                    "voice.transcribe requires payload.audioBase64.",
                    request_id=request.id,
                )
            try:
                text = await self._bridge.transcribe(audio_base64)
            except Exception:
                return _error_response("STT_FAILED", "Voice transcription failed.", request.id)
            return VoiceBridgeResponse(id=request.id, ok=True, payload={"text": text})

        return _error_response(
            "UNKNOWN_METHOD",
            f"Unknown method: {request.method}",
            request_id=request.id,
        )


class _ConnectionSender:
    def __init__(self, websocket: ServerConnection) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()

    async def send(self, message: str) -> None:
        async with self._lock:
            await self._websocket.send(message)

    async def emit(self, event: VoiceBridgeEvent | BridgeEvent) -> None:
        await self.send(event.model_dump_json(by_alias=True, exclude_none=True))


def _parse_request(raw_message: str | bytes) -> VoiceBridgeRequest:
    if isinstance(raw_message, bytes):
        raw_message = raw_message.decode("utf-8")
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise ValueError("Request must be valid JSON.") from exc
    try:
        return VoiceBridgeRequest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def _mode_from_payload(payload: dict[str, object]) -> VoiceLaunchMode:
    mode = payload.get("mode")
    if mode == "sharedSession":
        return "sharedSession"
    if mode == "parallelWorktree":
        return "parallelWorktree"
    if mode == "independentSession":
        return "independentSession"

    legacy_mode = payload.get("contextMode")
    if legacy_mode == "sharedContext":
        return "sharedSession"
    return "independentSession"


def _int_from_payload(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return 0


def _context_messages_from_payload(
    payload: dict[str, object],
    *,
    mode: VoiceLaunchMode,
) -> list[BaseMessage]:
    if mode not in {"sharedSession", "parallelWorktree"}:
        return []
    raw_messages = payload.get("contextMessages")
    if not isinstance(raw_messages, list):
        return []

    messages: list[BaseMessage] = []
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        role = raw_message.get("role")
        raw_content = raw_message.get("content")
        if not isinstance(raw_content, str):
            continue
        content = raw_content.strip()
        if not content:
            continue
        if role == "assistant":
            messages.append(AIMessage(content=content))
        elif role == "user":
            messages.append(HumanMessage(content=content))
    return messages


def _error_response(code: str, message: str, request_id: str = "") -> VoiceBridgeResponse:
    return VoiceBridgeResponse(
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
