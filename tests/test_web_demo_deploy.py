from voice_code.web_demo.handlers import allowed_origins as _allowed_origins


def test_allowed_origins_include_configured_public_origin() -> None:
    origins = _allowed_origins("127.0.0.1", configured=["http://192.0.2.10"])

    assert "http://192.0.2.10" in origins
    assert "http://127.0.0.1:5174" in origins
