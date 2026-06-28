"""Abort signal — cross-thread cancellation token."""

from __future__ import annotations

import threading


class AbortSignal:
    """线程安全的 abort 信号."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def trigger(self) -> None:
        """Set the abort signal."""
        self._event.set()

    def is_triggered(self) -> bool:
        """Check if abort has been triggered."""
        return self._event.is_set()

    def clear(self) -> None:
        """Reset the abort signal for reuse."""
        self._event.clear()
