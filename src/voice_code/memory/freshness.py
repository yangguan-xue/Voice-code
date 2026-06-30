from __future__ import annotations

from datetime import UTC, datetime, timedelta

from voice_code.memory.models import MemoryEntry


def is_fresh(entry: MemoryEntry, now: datetime | None = None) -> bool:
    if now is None:
        now = datetime.now(UTC)
    max_age = timedelta(days=entry.freshness_days)
    age = now - entry.updated_at
    return age < max_age


def is_stale(entry: MemoryEntry, now: datetime | None = None) -> bool:
    return not is_fresh(entry, now)


def days_until_stale(entry: MemoryEntry, now: datetime | None = None) -> int:
    if now is None:
        now = datetime.now(UTC)
    max_age = timedelta(days=entry.freshness_days)
    age = now - entry.updated_at
    remaining = max_age - age
    return max(0, remaining.days)


def filter_fresh(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    now = datetime.now(UTC)
    return [e for e in entries if is_fresh(e, now)]


def get_freshness_warning(entry: MemoryEntry) -> str | None:
    remaining = days_until_stale(entry)
    if remaining <= 0:
        return f"Memory '{entry.name}' is stale (updated {entry.updated_at.date()})"
    if remaining <= 7:
        return f"Memory '{entry.name}' will become stale in {remaining} days"
    return None
