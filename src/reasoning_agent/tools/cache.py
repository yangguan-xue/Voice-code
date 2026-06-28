"""Read-before-write cache — tracks which files have been read"""

from __future__ import annotations

from pathlib import Path

_cache: dict[str, bool] = {}


def mark_as_read(file_path: str) -> None:
    """标记文件已被 Read 工具读取。"""
    _cache[str(Path(file_path).resolve())] = True


def was_read(file_path: str) -> bool:
    """检查文件是否已被 Read 工具读取。"""
    return _cache.get(str(Path(file_path).resolve()), False)
