"""Helpers for starting desktop subprocesses."""

from __future__ import annotations

import json
from typing import Any


def serialize_startup_json(payload: Any) -> str:
    """Serialize a startup payload without non-ASCII stdout bytes."""
    return json.dumps(payload, ensure_ascii=True)
