"""Tests for the gateway-backed image_edit tool."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

from voice_code.tools.image_edit import image_edit

image_edit_module = sys.modules["voice_code.tools.image_edit"]
image_gen_module = sys.modules["voice_code.tools.image_gen"]


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GW_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("GW_IMAGE_API_KEY", raising=False)
    monkeypatch.delenv("GW_IMAGE_MODEL", raising=False)
    monkeypatch.setattr(image_gen_module, "_CONFIG_PATH", tmp_path / "gateway-image.json")


def _write_config() -> None:
    image_gen_module._CONFIG_PATH.write_text(
        '{"base_url": "https://gateway.example/v1", "api_key": "secret-token"}',
        encoding="utf-8",
    )


def _ref_image(tmp_path: Path, name: str = "ref.png") -> str:
    target = tmp_path / name
    target.write_bytes(b"\x89PNG\r\n\x1a\nref")
    return str(target)


def test_image_edit_requires_reference_images(tmp_path: Path) -> None:
    result = image_edit.invoke(
        {"prompt": "make it blue", "image_paths": [], "output_path": str(tmp_path / "o.png")}
    )
    assert "1-4" in result


def test_image_edit_rejects_missing_reference(tmp_path: Path) -> None:
    result = image_edit.invoke(
        {
            "prompt": "make it blue",
            "image_paths": [str(tmp_path / "nope.png")],
            "output_path": str(tmp_path / "o.png"),
        }
    )
    assert "not found" in result


def test_image_edit_refuses_to_overwrite(tmp_path: Path) -> None:
    ref = _ref_image(tmp_path)
    target = tmp_path / "exists.png"
    target.write_bytes(b"old")
    result = image_edit.invoke(
        {"prompt": "x", "image_paths": [ref], "output_path": str(target)}
    )
    assert "already exists" in result


def test_multipart_body_contains_fields_and_files() -> None:
    body, boundary = image_edit_module._build_multipart_body(
        [("model", "m"), ("prompt", "hello")],
        [("image", "a.png", "image/png", b"PNGDATA")],
    )
    text = body.decode("latin-1")
    assert f"--{boundary}" in text
    assert 'name="model"' in text and "\r\n\r\nm\r\n" in text
    assert 'name="prompt"' in text and "hello" in text
    assert 'name="image"; filename="a.png"' in text
    assert "Content-Type: image/png" in text
    assert b"PNGDATA" in body
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_image_edit_saves_gateway_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config()
    ref = _ref_image(tmp_path)
    image_bytes = b"edited-image-bytes"
    captured: dict[str, object] = {}

    def fake_request(config, fields, files):
        captured["fields"] = fields
        captured["files"] = files
        return {"data": [{"b64_json": base64.b64encode(image_bytes).decode("ascii")}]}

    monkeypatch.setattr(image_edit_module, "_request_edit", fake_request)

    target = tmp_path / "out" / "edited.png"
    result = image_edit.invoke(
        {
            "prompt": "turn the cat into a dog",
            "image_paths": [ref],
            "output_path": str(target),
            "quality": "high",
        }
    )

    assert target.read_bytes() == image_bytes
    assert str(target) in result
    assert "secret-token" not in result
    assert ("prompt", "turn the cat into a dog") in captured["fields"]
    assert ("quality", "high") in captured["fields"]
    files = captured["files"]
    assert len(files) == 1
    assert files[0][0] == "image"
    assert files[0][3] == b"\x89PNG\r\n\x1a\nref"


def test_image_edit_multiple_references_use_array_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config()
    ref1 = _ref_image(tmp_path, "a.png")
    ref2 = _ref_image(tmp_path, "b.png")
    captured: dict[str, object] = {}

    def fake_request(config, fields, files):
        captured["files"] = files
        return {"data": [{"b64_json": base64.b64encode(b"x").decode("ascii")}]}

    monkeypatch.setattr(image_edit_module, "_request_edit", fake_request)
    image_edit.invoke(
        {
            "prompt": "combine",
            "image_paths": [ref1, ref2],
            "output_path": str(tmp_path / "o.png"),
        }
    )
    assert [f[0] for f in captured["files"]] == ["image[]", "image[]"]


def test_image_edit_uses_config_size_and_quality_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_gen_module._CONFIG_PATH.write_text(
        '{"base_url": "https://gateway.example/v1", "api_key": "k",'
        ' "size": "2048x2048", "quality": "high"}',
        encoding="utf-8",
    )
    ref = _ref_image(tmp_path)
    captured: dict[str, object] = {}

    def fake_request(config, fields, files):
        captured["fields"] = fields
        return {"data": [{"b64_json": base64.b64encode(b"x").decode("ascii")}]}

    monkeypatch.setattr(image_edit_module, "_request_edit", fake_request)
    image_edit.invoke(
        {"prompt": "restyle", "image_paths": [ref], "output_path": str(tmp_path / "o.png")}
    )
    assert ("size", "2048x2048") in captured["fields"]
    assert ("quality", "high") in captured["fields"]
