from __future__ import annotations

import io

import pytest

from voice_code.memory.select import rerank_candidates
from voice_code.telemetry import configure_logging
from voice_code.voice.classifier import CommandClassifier
from voice_code.voice.wakeword import WakeWordDetector


@pytest.mark.asyncio
async def test_voice_and_memory_diagnostics_do_not_log_user_content() -> None:
    stream = io.StringIO()
    configure_logging(debug=True, json_output=True, stream=stream, force=True)

    wake_phrase = "private wake phrase"
    spoken_text = f"prefix {wake_phrase} suffix"
    detector = WakeWordDetector([wake_phrase])
    assert await detector.detect_from_bytes(b"audio", spoken_text) is True

    classifier = CommandClassifier()
    await classifier.classify("private natural language request")

    rerank_candidates(
        "private memory query",
        [
            {
                "name": "private memory title",
                "description": "private memory description",
                "content": "private memory body",
                "tags": ["private memory tag"],
            }
        ],
    )

    output = stream.getvalue()
    assert wake_phrase not in output
    assert spoken_text not in output
    assert "private natural language request" not in output
    assert "private memory query" not in output
    assert "private memory title" not in output
    assert "private memory description" not in output
    assert "private memory body" not in output
    assert "private memory tag" not in output
