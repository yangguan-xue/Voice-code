"""Error recovery helpers for truncation and overloaded models."""

from __future__ import annotations

MAX_OUTPUT_RECOVERY = 2
MAX_OUTPUT_RECOVERY_MSG = (
    "Output token limit hit. Resume directly — no apology, "
    "no recap of what you were doing. Pick up mid-thought if "
    "that is where the cut happened. Break remaining work "
    "into smaller pieces."
)


def get_finish_reason(message: object) -> str:
    """Extract finish_reason from an accumulated AI message/chunk."""
    metadata = getattr(message, "response_metadata", None)
    if isinstance(metadata, dict):
        return str(metadata.get("finish_reason", "") or "")
    return ""


def is_model_overloaded_error(error: Exception) -> bool:
    """Detect overloaded / unavailable model errors."""
    status_code = getattr(error, "status_code", None)
    if status_code in (429, 503, 529):
        return True

    body = str(error).lower()
    return any(pattern in body for pattern in ("overloaded", "capacity", "rate limit"))
