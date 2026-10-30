"""ImageGen tool — 通过私有网关生成图片（gateway-image 的原生工具版）。

Web demo 的 bash 跑在断网 Docker 沙盒里，无法执行 gateway-image skill
自带的脚本，因此把同样的能力做成 host 侧原生工具：凭据加载顺序、
API 路径与 skill 的 scripts/gen_image.py 完全一致。

凭据来源（先到先得，api_key 永不写入返回值或日志）：
  1. 环境变量 GW_IMAGE_BASE_URL / GW_IMAGE_API_KEY / GW_IMAGE_MODEL
  2. 配置文件 ~/.codex/gateway-image.json {"base_url", "api_key", "model"}
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from langchain_core.tools import tool

from voice_code.security import WorkspaceBoundaryError, resolve_workspace_path

_CONFIG_PATH = Path.home() / ".codex" / "gateway-image.json"
_DEFAULT_MODEL = "gpt-image-2.5-flare"
_DEFAULT_SIZE = "1024x1024"
_DEFAULT_QUALITY = "high"
_TIMEOUT_SECONDS = 600
_ALLOWED_SIZES = (
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "2048x1152",
    "1152x2048",
    "2048x2048",
    "auto",
)
_ALLOWED_QUALITIES = ("low", "medium", "high")
_ALLOWED_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


class _GatewayConfigError(RuntimeError):
    pass


def _load_gateway_config() -> dict[str, str]:
    file_config: dict[str, str] = {}
    if _CONFIG_PATH.is_file():
        try:
            file_config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _GatewayConfigError(f"cannot read {_CONFIG_PATH}: {exc}") from exc
    base_url = os.getenv("GW_IMAGE_BASE_URL") or str(file_config.get("base_url") or "")
    api_key = os.getenv("GW_IMAGE_API_KEY") or str(file_config.get("api_key") or "")
    model = os.getenv("GW_IMAGE_MODEL") or str(file_config.get("model") or "") or _DEFAULT_MODEL
    size = os.getenv("GW_IMAGE_SIZE") or str(file_config.get("size") or "") or _DEFAULT_SIZE
    quality = (
        os.getenv("GW_IMAGE_QUALITY") or str(file_config.get("quality") or "") or _DEFAULT_QUALITY
    )
    if not base_url:
        raise _GatewayConfigError(
            f"gateway base URL not configured; set GW_IMAGE_BASE_URL or create {_CONFIG_PATH}"
        )
    if not api_key:
        raise _GatewayConfigError(
            f"gateway token not configured; set GW_IMAGE_API_KEY or create {_CONFIG_PATH}"
        )
    return {
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "size": size,
        "quality": quality,
    }


def _request_generation(config: dict[str, str], payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        f"{config['base_url']}/images/generations",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(body).get("error", {}).get("message") or body
        except json.JSONDecodeError:
            message = body
        raise _GatewayConfigError(f"gateway returned HTTP {exc.code}: {message[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise _GatewayConfigError(f"gateway request failed: {exc}") from exc


def _extract_image_bytes(item: dict[str, object]) -> bytes:
    if item.get("b64_json"):
        return base64.b64decode(str(item["b64_json"]))
    if item.get("url"):
        with urllib.request.urlopen(str(item["url"]), timeout=120) as response:
            return response.read()
    raise _GatewayConfigError("gateway returned an image without b64_json or url")


@tool
def image_gen(
    prompt: str,
    output_path: str,
    size: str = "",
    quality: str = "",
    model: str = "",
) -> str:
    """Generate an image through the private image gateway and save it into the workspace.

    Use this tool when the user asks to create, draw, or generate a picture.
    Generation usually takes 30-120 seconds; that is normal.

    Map aspect ratio requests to exact sizes: 16:9 -> "2048x1152", 9:16 -> "1152x2048",
    3:2 -> "1536x1024", 2:3 -> "1024x1536", square -> "1024x1024" (or "2048x2048"
    for a larger square). Quality and resolution are independent; use "high" quality
    for final assets and "low" only for drafts or quick previews.

    Args:
        prompt: Text description of the image to generate.
        output_path: Workspace-relative file path to save to, e.g. "output/cat.png".
            Existing files are never overwritten; choose a new name instead.
        size: Optional exact size (e.g. "2048x1152"); defaults to the configured size.
        quality: Optional "low" (drafts), "medium", or "high" (finals);
            defaults to the configured quality.
        model: Optional gateway model id; defaults to the configured model.
    """
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

    try:
        config = _load_gateway_config()
    except _GatewayConfigError as exc:
        return f"<tool_use_error>Error: {exc}</tool_use_error>"
    effective_size = size or config["size"]
    effective_quality = quality or config["quality"]
    if effective_size not in _ALLOWED_SIZES:
        return (
            f"<tool_use_error>Error: size must be one of {', '.join(_ALLOWED_SIZES)}"
            "</tool_use_error>"
        )
    if effective_quality not in _ALLOWED_QUALITIES:
        return (
            f"<tool_use_error>Error: quality must be one of {', '.join(_ALLOWED_QUALITIES)}"
            "</tool_use_error>"
        )

    try:
        payload: dict[str, object] = {
            "model": model or config["model"],
            "prompt": prompt,
            "n": 1,
            "size": effective_size,
        }
        if effective_quality:
            payload["quality"] = effective_quality
        result = _request_generation(config, payload)
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

    return f"Saved generated image to {target}"


image_gen.metadata = {
    "is_readonly": False,
    "is_concurrency_safe": False,
}
