"""Threaded runner helpers for desktop agent turns."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

from voice_code.agent.abort import AbortSignal
from voice_code.agent.types import AgentEvent
from voice_code.desktop.bridge import AgentTurnRunner

_QueueItem = AgentEvent | BaseException | None


def _enqueue_threadsafe(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[_QueueItem],
    item: _QueueItem,
) -> None:
    try:
        loop.call_soon_threadsafe(queue.put_nowait, item)
    except RuntimeError:
        if not loop.is_closed():
            raise


async def run_turn_in_worker_thread(
    runner: AgentTurnRunner,
    text: str,
    *,
    abort_signal: AbortSignal,
    **kwargs: object,
) -> AsyncIterator[AgentEvent]:
    """Run an async agent turn generator in a worker thread.

    The desktop WebSocket loop must remain available while the agent waits for
    synchronous tool permission approval. This adapter keeps blocking approval
    work out of the transport event loop while preserving the async generator
    shape consumed by ``DesktopAgentBridge``.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[_QueueItem] = asyncio.Queue()

    def enqueue(item: _QueueItem) -> None:
        _enqueue_threadsafe(loop, queue, item)

    def worker() -> None:
        async def run() -> None:
            async for event in runner(text, abort_signal=abort_signal, **kwargs):
                enqueue(event)

        try:
            asyncio.run(run())
        except BaseException as exc:
            enqueue(exc)
        finally:
            enqueue(None)

    thread = threading.Thread(
        target=worker,
        name="reasoning-desktop-agent-turn",
        daemon=True,
    )
    thread.start()

    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        if thread.is_alive():
            abort_signal.trigger()
