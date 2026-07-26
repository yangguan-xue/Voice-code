"""Typed protocol for the desktop voice companion bridge."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from voice_code.desktop.protocol import BridgeEvent

VoiceStateName = Literal[
    "idle",
    "listening",
    "transcribing",
    "thinking",
    "speaking",
    "paused",
    "error",
]

VoiceEventName = Literal[
    "voice.state",
    "voice.user.partial",
    "voice.user.final",
    "voice.assistant.delta",
    "voice.assistant.final",
    "voice.error",
]

VoiceRequestMethod = Literal[
    "voice.bootstrap",
    "voice.turn.start",
    "voice.turn.interrupt",
    "voice.transcribe",
    "voice.tts.setMuted",
    "permission.resolve",
]

VoiceLaunchMode = Literal["sharedSession", "independentSession", "parallelWorktree"]

class VoiceBridgeEvent(BaseModel):
    """Stable voice event envelope consumed by the desktop voice window."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["event"] = "event"
    event: VoiceEventName
    session_id: str = Field(alias="sessionId")
    turn_id: int | None = Field(default=None, alias="turnId")
    payload: dict[str, object] = Field(default_factory=dict)


VoiceTransportEvent = VoiceBridgeEvent | BridgeEvent
VoiceEmitter = Callable[[VoiceTransportEvent], None | Awaitable[None]]


class VoiceBridgeRequest(BaseModel):
    type: Literal["request"] = "request"
    id: str
    method: VoiceRequestMethod
    token: str
    payload: dict[str, object] = Field(default_factory=dict)


class VoiceBridgeError(BaseModel):
    code: str
    message: str


class VoiceBridgeResponse(BaseModel):
    type: Literal["response"] = "response"
    id: str = ""
    ok: bool
    payload: dict[str, object] | None = None
    error: VoiceBridgeError | None = None


def voice_state_event(
    *,
    session_id: str,
    state: VoiceStateName,
    text: str = "",
    turn_id: int | None = None,
) -> VoiceBridgeEvent:
    payload: dict[str, object] = {"state": state}
    if text:
        payload["text"] = text
    return VoiceBridgeEvent(
        event="voice.state",
        session_id=session_id,
        turn_id=turn_id,
        payload=payload,
    )


def voice_text_event(
    *,
    session_id: str,
    event: Literal[
        "voice.user.partial",
        "voice.user.final",
        "voice.assistant.delta",
        "voice.assistant.final",
    ],
    text: str,
    turn_id: int | None = None,
) -> VoiceBridgeEvent:
    return VoiceBridgeEvent(
        event=event,
        session_id=session_id,
        turn_id=turn_id,
        payload={"text": text},
    )


def voice_error_event(
    *,
    session_id: str,
    message: str,
    recoverable: bool = True,
    turn_id: int | None = None,
) -> VoiceBridgeEvent:
    return VoiceBridgeEvent(
        event="voice.error",
        session_id=session_id,
        turn_id=turn_id,
        payload={"message": message, "recoverable": recoverable},
    )
