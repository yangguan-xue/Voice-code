"""Content-addressed cache for assembled system prompts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from voice_code.platform_fs import best_effort_private_permissions

_MEMORY_CACHE: dict[str, str] = {}
_MAX_ENTRIES = 32


def cached_prompt(
    *,
    workspace: str | Path,
    inputs: dict[str, object],
    builder: Callable[[], str],
) -> str:
    serialized = json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str)
    key = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    cached = _MEMORY_CACHE.get(key)
    if cached is not None:
        return cached

    cache_dir = Path(workspace).resolve() / ".reasoning" / "cache" / "prompts"
    cache_path = cache_dir / f"{key}.txt"
    try:
        cached = cache_path.read_text(encoding="utf-8")
    except OSError:
        cached = builder()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            cache_path.write_text(cached, encoding="utf-8")
            best_effort_private_permissions(cache_path)
        except OSError:
            pass

    if len(_MEMORY_CACHE) >= _MAX_ENTRIES:
        _MEMORY_CACHE.pop(next(iter(_MEMORY_CACHE)))
    _MEMORY_CACHE[key] = cached
    return cached
