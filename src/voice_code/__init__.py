from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("voice-code")
except PackageNotFoundError:
    # Frozen launchers do not carry wheel metadata unless it is added explicitly.
    __version__ = "0.2.0"
