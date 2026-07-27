"""Core local desktop bridge around an agent turn runner."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from voice_code.agent.abort import AbortSignal
from voice_code.agent.types import AgentEvent
from voice_code.desktop.protocol import (
    BridgeEmitter,
    BridgeEvent,
    TurnStartResult,
    make_turn_started_event,
    map_agent_event,
)

AgentTurnRunner = Callable[..., AsyncIterator[AgentEvent]]


@dataclass(slots=True)
class _ActiveTurn:
    turn_id: int
    abort_signal: AbortSignal


class DesktopAgentBridge:
    """Serialize desktop turns and fan internal agent events out as bridge events."""

    def __init__(
        self,
        *,
        session_id: str,
        runner: AgentTurnRunner,
        emit: BridgeEmitter | None = None,
    ) -> None:
        self.session_id = session_id
        self._runner = runner
        self._emit = emit
        self._turn_lock = asyncio.Lock()
        self._next_turn_id = 1
        self._active_turn: _ActiveTurn | None = None

    async def start_turn(
        self,
        text: str,
        *,
        permission_mode: str | None = None,
    ) -> TurnStartResult:
        user_text = text.strip()
        if not user_text:
            raise ValueError("turn text must not be empty")

        async with self._turn_lock:
            turn_id = self._next_turn_id
            self._next_turn_id += 1
            abort_signal = AbortSignal()
            self._active_turn = _ActiveTurn(turn_id=turn_id, abort_signal=abort_signal)
            finish_reason = ""
            await self._emit_event(
                make_turn_started_event(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    user_text=user_text,
                )
            )

            try:
                runner_kwargs: dict[str, object] = {"abort_signal": abort_signal}
                if permission_mode:
                    runner_kwargs["permission_mode"] = permission_mode
                async for agent_event in self._runner(user_text, **runner_kwargs):
                    agent_event.turn = turn_id
                    await self._emit_event(map_agent_event(self.session_id, agent_event))
                    if agent_event.finish_reason:
                        finish_reason = agent_event.finish_reason
            finally:
                self._active_turn = None

            return TurnStartResult(
                session_id=self.session_id,
                turn_id=turn_id,
                finish_reason=finish_reason,
            )

    def interrupt_turn(self, *, turn_id: int | None = None) -> bool:
        active = self._active_turn
        if active is None:
            return False
        if turn_id is not None and active.turn_id != turn_id:
            return False
        active.abort_signal.trigger()
        return True

    @property
    def is_busy(self) -> bool:
        return self._active_turn is not None

    def switch_session(self, session_id: str, *, next_turn_id: int = 1) -> None:
        if self.is_busy:
            raise ValueError("Cannot switch sessions while a turn is running.")
        self.session_id = session_id
        self._next_turn_id = max(next_turn_id, 1)

    def set_emit(self, emit: BridgeEmitter | None) -> BridgeEmitter | None:
        previous = self._emit
        self._emit = emit
        return previous

    async def emit_event(self, event: BridgeEvent) -> None:
        await self._emit_event(event)

    async def _emit_event(self, event: BridgeEvent) -> None:
        if self._emit is None:
            return
        result = self._emit(event)
        if result is not None:
            await result
