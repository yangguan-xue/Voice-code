import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "voice: mark test as voice-related (skipped in CI without audio hardware)",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "voice" in item.nodeid or "audio" in item.nodeid or "sounddevice" in item.nodeid:
            item.add_marker(pytest.mark.skip(reason="requires audio hardware (PortAudio)"))
