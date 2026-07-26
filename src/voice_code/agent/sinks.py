"""Side-effect sinks used by the agent runtime coordinator."""

from __future__ import annotations

from langchain_core.messages import BaseMessage

from voice_code.session.transcript import TranscriptWriter
from voice_code.telemetry import start_span


class TranscriptSink:
    def __init__(self, writer: TranscriptWriter | None) -> None:
        self._writer = writer

    @property
    def enabled(self) -> bool:
        return self._writer is not None

    def write(self, message: BaseMessage) -> None:
        if self._writer is not None:
            with start_span("transcript.persist", {"operation": "write"}):
                self._writer.write_message(message)

    def write_many(self, messages: list[BaseMessage]) -> None:
        for message in messages:
            self.write(message)
