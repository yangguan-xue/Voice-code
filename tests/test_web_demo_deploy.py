from voice_code.web_demo.server import _allowed_origins


def test_allowed_origins_include_configured_public_origin() -> None:
    origins = _allowed_origins("127.0.0.1", configured=["http://118.25.44.28"])

    assert "http://118.25.44.28" in origins
    assert "http://127.0.0.1:5174" in origins
