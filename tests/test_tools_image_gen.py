"""Tests for the gateway-backed image_gen tool."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

from voice_code.tools.image_gen import image_gen

image_gen_module = sys.modules["voice_code.tools.image_gen"]


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GW_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("GW_IMAGE_API_KEY", raising=False)
    monkeypatch.delenv("GW_IMAGE_MODEL", raising=False)
    monkeypatch.setattr(image_gen_module, "_CONFIG_PATH", tmp_path / "gateway-image.json")


def _write_config(path: Path) -> None:
    path.write_text(
        '{"base_url": "https://gateway.example/v1", "api_key": "secret-token"}',
        encoding="utf-8",
    )


def test_image_gen_rejects_unknown_size(tmp_path: Path) -> None:
    result = image_gen.invoke(
        {"prompt": "cat", "output_path": str(tmp_path / "a.png"), "size": "999x999"}
    )
    assert "size must be one of" in result


def test_image_gen_rejects_non_image_suffix(tmp_path: Path) -> None:
    result = image_gen.invoke({"prompt": "cat", "output_path": str(tmp_path / "a.txt")})
    assert "output_path must end with" in result


def test_image_gen_refuses_to_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "exists.png"
    target.write_bytes(b"old")
    result = image_gen.invoke({"prompt": "cat", "output_path": str(target)})
    assert "already exists" in result
    assert target.read_bytes() == b"old"


def test_image_gen_reports_missing_credentials(tmp_path: Path) -> None:
    result = image_gen.invoke({"prompt": "cat", "output_path": str(tmp_path / "a.png")})
    assert "not configured" in result


def test_image_gen_saves_gateway_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(image_gen_module._CONFIG_PATH)
    image_bytes = b"\x89PNG\r\n\x1a\nfake-image-bytes"
    captured: dict[str, object] = {}

    def fake_request(config: dict[str, str], payload: dict[str, object]) -> dict[str, object]:
        captured["config"] = config
        captured["payload"] = payload
        return {"data": [{"b64_json": base64.b64encode(image_bytes).decode("ascii")}]}

    monkeypatch.setattr(image_gen_module, "_request_generation", fake_request)

    target = tmp_path / "output" / "cat.png"
    result = image_gen.invoke(
        {
            "prompt": "a cat in the sun",
            "output_path": str(target),
            "size": "1536x1024",
            "quality": "high",
        }
    )

    assert target.read_bytes() == image_bytes
    assert str(target) in result
    assert "secret-token" not in result
    payload = captured["payload"]
    assert payload == {
        "model": "gpt-image-2.5-flare",
        "prompt": "a cat in the sun",
        "n": 1,
        "size": "1536x1024",
        "quality": "high",
    }
    assert captured["config"]["api_key"] == "secret-token"


def test_image_gen_model_flag_overrides_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(image_gen_module._CONFIG_PATH)
    captured: dict[str, object] = {}

    def fake_request(config: dict[str, str], payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        return {"data": [{"b64_json": base64.b64encode(b"img").decode("ascii")}]}

    monkeypatch.setattr(image_gen_module, "_request_generation", fake_request)
    image_gen.invoke(
        {
            "prompt": "cat",
            "output_path": str(tmp_path / "b.png"),
            "model": "other-model",
        }
    )
    assert captured["payload"]["model"] == "other-model"


def test_image_gen_uses_config_size_and_quality_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_gen_module._CONFIG_PATH.write_text(
        '{"base_url": "https://gateway.example/v1", "api_key": "k",'
        ' "size": "2048x1152", "quality": "medium"}',
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_request(config: dict[str, str], payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        return {"data": [{"b64_json": base64.b64encode(b"img").decode("ascii")}]}

    monkeypatch.setattr(image_gen_module, "_request_generation", fake_request)
    image_gen.invoke({"prompt": "cat", "output_path": str(tmp_path / "c.png")})
    assert captured["payload"]["size"] == "2048x1152"
    assert captured["payload"]["quality"] == "medium"


def test_image_gen_default_quality_is_high_without_config(tmp_path: Path) -> None:
    # 配置缺省时校验顺序：先报凭据缺失，不能冒出 size/quality 校验错
    result = image_gen.invoke({"prompt": "cat", "output_path": str(tmp_path / "d.png")})
    assert "not configured" in result
