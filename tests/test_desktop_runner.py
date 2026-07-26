from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator

import pytest

from voice_code.agent.abort import AbortSignal
from voice_code.agent.types import AgentEvent, EventType
from voice_code.desktop.runner import _enqueue_threadsafe, run_turn_in_worker_thread


def test_enqueue_threadsafe_ignores_closed_event_loop() -> None:
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[AgentEvent | BaseException | None] = asyncio.Queue()
    loop.close()

    _enqueue_threadsafe(loop, queue, None)

    assert queue.empty()


@pytest.mark.asyncio
async def test_run_turn_in_worker_thread_streams_events_from_worker() -> None:
    thread_ids: list[int] = []

    async def runner(
        _text: str,
        *,
        abort_signal: AbortSignal,
    ) -> AsyncIterator[AgentEvent]:
        assert not abort_signal.is_triggered()
        thread_ids.append(threading.get_ident())
        yield AgentEvent(type=EventType.TEXT, turn=1, content="hello")
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

    events = [
        event
        async for event in run_turn_in_worker_thread(
            runner,
            "hi",
            abort_signal=AbortSignal(),
        )
    ]

    assert thread_ids
    assert thread_ids[0] != threading.get_ident()
    assert [event.type for event in events] == [EventType.TEXT, EventType.FINISH]


@pytest.mark.asyncio
async def test_run_turn_in_worker_thread_keeps_event_loop_responsive() -> None:
    async def runner(
        _text: str,
        *,
        abort_signal: AbortSignal,
    ) -> AsyncIterator[AgentEvent]:
        while not abort_signal.is_triggered():
            time.sleep(0.005)
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="interrupted")

    abort_signal = AbortSignal()
    events: list[AgentEvent] = []

    async def collect() -> None:
        async for event in run_turn_in_worker_thread(
            runner,
            "wait",
            abort_signal=abort_signal,
        ):
            events.append(event)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.02)
    abort_signal.trigger()
    await asyncio.wait_for(task, timeout=1)

    assert events[-1].finish_reason == "interrupted"


@pytest.mark.asyncio
async def test_run_turn_in_worker_thread_propagates_runner_errors() -> None:
    async def runner(
        _text: str,
        *,
        abort_signal: AbortSignal,
    ) -> AsyncIterator[AgentEvent]:
        assert not abort_signal.is_triggered()
        raise RuntimeError("runner failed")
        yield AgentEvent(type=EventType.TEXT, turn=1, content="unreachable")

    with pytest.raises(RuntimeError, match="runner failed"):
        async for _event in run_turn_in_worker_thread(
            runner,
            "boom",
            abort_signal=AbortSignal(),
        ):
            pass
