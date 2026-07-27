from __future__ import annotations

from datetime import UTC, datetime, timedelta

from voice_code.memory.freshness import is_fresh, is_stale
from voice_code.memory.models import MemoryEntry


def _entry(updated_at: datetime, freshness_days: int = 30) -> MemoryEntry:
    return MemoryEntry(
        id="test-1",
        name="test",
        updated_at=updated_at,
        freshness_days=freshness_days,
    )


def test_is_stale_when_fresh_returns_false():
    entry = _entry(updated_at=datetime.now(UTC))
    assert is_stale(entry) is False


def test_is_stale_when_expired_returns_true():
    entry = _entry(
        updated_at=datetime.now(UTC) - timedelta(days=31),
        freshness_days=30,
    )
    assert is_stale(entry) is True


def test_is_stale_and_is_fresh_are_inverse():
    now = datetime.now(UTC)
    for offset in range(-5, 35):
        entry = _entry(updated_at=now - timedelta(days=offset))
        assert is_stale(entry) is not is_fresh(entry)
