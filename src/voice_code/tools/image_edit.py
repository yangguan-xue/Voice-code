"""ImageEdit tool — 通过私有网关编辑图片（参考图/局部重绘）。

与 image_gen 共用网关凭据（见 image_gen.py 模块文档）。编辑接口是
multipart 表单（OpenAI 兼容 /images/edits），这里用 stdlib 手拼 body，
与 gateway-image skill 的 scripts/gen_image.py 保持同一套协议。
"""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from langchain_core.tools import tool

from voice_code.security import WorkspaceBoundaryError, resolve_workspace_path
from voice_code.tools.image_gen import (
    _ALLOWED_QUALITIES,
    _ALLOWED_SIZES,
    _ALLOWED_SUFFIXES,
    _extract_image_bytes,
    _GatewayConfigError,
    _load_gateway_config,
)

_MAX_REFERENCE_IMAGES = 4
_MAX_REFERENCE_BYTES = 8 * 1024 * 1024
_TIMEOUT_SECONDS = 600


def _build_multipart_body(
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, str, bytes]],
) -> tuple[bytes, str]:
    """拼 multipart/form-data。files: (field_name, filename, content_type, data)。"""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, value in fields:
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            f"\r\n\r\n{value}\r\n"
        ).encode()
    for name, filename, content_type, data in files:
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
        ).encode()
        body += data
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), boundary


def _request_edit(
    config: dict[str, str],
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, str, bytes]],
) -> dict[str, object]:
    body, boundary = _build_multipart_body(fields, files)
    request = urllib.request.Request(
        f"{config['base_url']}/images/edits",
        data=body,
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(detail).get("error", {}).get("message") or detail
        except json.JSONDecodeError:
            message = detail
        raise _GatewayConfigError(f"gateway returned HTTP {exc.code}: {message[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise _GatewayConfigError(f"gateway request failed: {exc}") from exc


def _load_reference_image(raw_path: str) -> tuple[Path, bytes] | str:
    """读取并校验参考图，成功返回 (路径, 字节)，失败返回错误文本。"""
    try:
        path = resolve_workspace_path(raw_path)
    except WorkspaceBoundaryError as exc:
        return f"<tool_use_error>Error: {exc}</tool_use_error>"
    if not path.is_file():
        return f"<tool_use_error>Error: reference image not found: {raw_path}</tool_use_error>"
    if path.suffix.lower() not in _ALLOWED_SUFFIXES:
        return (
            f"<tool_use_error>Error: reference image must end with one of "
            f"{', '.join(_ALLOWED_SUFFIXES)}</tool_use_error>"
        )
    data = path.read_bytes()
    if len(data) > _MAX_REFERENCE_BYTES:
        return f"<tool_use_error>Error: reference image too large: {raw_path}</tool_use_error>"
    return path, data


@tool
def image_edit(
    prompt: str,
    image_paths: list[str],
    output_path: str,
    size: str = "",
    quality: str = "",
    model: str = "",
    mask_path: str = "",
) -> str:
    """Edit image(s) through the private image gateway and save the result into the workspace.

    Use this when the user asks to modify, restyle, or combine existing images.
    Generation usually takes 30-120 seconds; that is normal.

    Args:
        prompt: Text description of the desired edit.
        image_paths: Workspace-relative paths of reference images (1-4 files).
        output_path: Workspace-relative file path to save to, e.g. "output/edited.png".
            Existing files are never overwritten; choose a new name instead.
        size: Optional exact size (e.g. "2048x1152" for 16:9, "1152x2048" for 9:16);
            defaults to the configured size.
        quality: Optional "low" (drafts), "medium", or "high" (finals);
            defaults to the configured quality.
        model: Optional gateway model id; defaults to the configured model.
        mask_path: Optional workspace-relative mask image for inpainting.
    """
    if not image_paths or len(image_paths) > _MAX_REFERENCE_IMAGES:
        return (
            f"<tool_use_error>Error: image_paths must contain 1-{_MAX_REFERENCE_IMAGES} "
            "workspace-relative image paths</tool_use_error>"
        )
    if size and size not in _ALLOWED_SIZES:
        return (
            f"<tool_use_error>Error: size must be one of {', '.join(_ALLOWED_SIZES)}"
            "</tool_use_error>"
        )
    if quality and quality not in _ALLOWED_QUALITIES:
        return (
            f"<tool_use_error>Error: quality must be one of {', '.join(_ALLOWED_QUALITIES)}"
            "</tool_use_error>"
        )
    try:
        target = resolve_workspace_path(output_path)
    except WorkspaceBoundaryError as exc:
        return f"<tool_use_error>Error: {exc}</tool_use_error>"
    if target.suffix.lower() not in _ALLOWED_SUFFIXES:
        return (
            f"<tool_use_error>Error: output_path must end with one of "
            f"{', '.join(_ALLOWED_SUFFIXES)}</tool_use_error>"
        )
    if target.exists():
        return (
            f"<tool_use_error>Error: {output_path} already exists; "
            "choose a new file name instead of overwriting</tool_use_error>"
        )

    references: list[tuple[Path, bytes]] = []
    for raw_path in image_paths:
        loaded = _load_reference_image(raw_path)
        if isinstance(loaded, str):
            return loaded
        references.append(loaded)
    mask: tuple[Path, bytes] | None = None
    if mask_path:
        loaded_mask = _load_reference_image(mask_path)
        if isinstance(loaded_mask, str):
            return loaded_mask
        mask = loaded_mask

    field_name = "image" if len(references) == 1 else "image[]"
    files: list[tuple[str, str, str, bytes]] = [
        (
            field_name,
            path.name,
            mimetypes.guess_type(path.name)[0] or "image/png",
            data,
        )
        for path, data in references
    ]
    if mask is not None:
        mask_file, mask_bytes = mask
        files.append(
            (
                "mask",
                mask_file.name,
                mimetypes.guess_type(mask_file.name)[0] or "image/png",
                mask_bytes,
            )
        )

    try:
        config = _load_gateway_config()
        fields = [
            ("model", model or config["model"]),
            ("prompt", prompt),
            ("n", "1"),
            ("size", size or config["size"]),
        ]
        effective_quality = quality or config["quality"]
        if effective_quality:
            fields.append(("quality", effective_quality))
        result = _request_edit(config, fields, files)
        data = result.get("data")
        if not isinstance(data, list) or not data:
            raise _GatewayConfigError("gateway returned no images")
        image_bytes = _extract_image_bytes(data[0])
    except _GatewayConfigError as exc:
        return f"<tool_use_error>Error: {exc}</tool_use_error>"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image_bytes)
    except OSError as exc:
        return f"<tool_use_error>Error writing image: {exc}</tool_use_error>"

    return f"Saved edited image to {target}"


image_edit.metadata = {
    "is_readonly": False,
    "is_concurrency_safe": False,
}
