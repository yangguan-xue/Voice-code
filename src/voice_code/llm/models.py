"""LLM 模型工厂 — 支持 .env 和 models.toml profiles。"""

import os
import tomllib
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
_ENV_FILE = _ROOT_DIR / ".env"
_MODELS_FILE = _ROOT_DIR / "models.toml"


def _read_env_file(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_file.exists():
        return values
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


def _load_dotenv() -> None:
    """加载 .env 文件到 os.environ（不覆盖已有环境变量）。"""
    for key, val in _read_env_file(_ENV_FILE).items():
        if key not in os.environ:
            os.environ[key] = val


def _load_profiles() -> dict[str, dict[str, Any]]:
    """读取 models.toml 中的 profiles。"""
    if not _MODELS_FILE.exists():
        return {}

    data = tomllib.loads(_MODELS_FILE.read_text(encoding="utf-8"))
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for name, value in profiles.items():
        if isinstance(name, str) and isinstance(value, dict):
            result[name] = value
    return result


def list_model_profiles() -> list[str]:
    """列出可用 profile 名称。"""
    return sorted(_load_profiles().keys())


def _resolve_profile(
    profile: str | None,
    env_values: dict[str, str],
) -> dict[str, str | int]:
    """解析单个 profile。"""
    if not profile:
        return {}

    profiles = _load_profiles()
    if profile not in profiles:
        available = ", ".join(sorted(profiles)) or "(none)"
        raise ValueError(
            f"Unknown model profile: {profile}. Available profiles: {available}"
        )

    raw = profiles[profile]
    resolved: dict[str, str | int] = {}

    model_name = raw.get("model_name")
    base_url = raw.get("base_url")
    api_key = raw.get("api_key")
    api_key_env = raw.get("api_key_env")
    fallback_profile = raw.get("fallback_profile")
    max_tokens = raw.get("max_tokens")

    if isinstance(model_name, str) and model_name:
        resolved["model_name"] = model_name
    if isinstance(base_url, str) and base_url:
        resolved["base_url"] = base_url
    if isinstance(api_key, str) and api_key:
        resolved["api_key"] = api_key
    elif isinstance(api_key_env, str) and api_key_env:
        resolved["api_key"] = os.getenv(api_key_env) or env_values.get(api_key_env, "")
    if isinstance(fallback_profile, str) and fallback_profile:
        resolved["fallback_profile"] = fallback_profile
    if isinstance(max_tokens, int) and max_tokens > 0:
        resolved["max_tokens"] = max_tokens

    return resolved


def init_model(
    *,
    profile: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.0,
    timeout: float = 60.0,
) -> ChatOpenAI:
    """初始化 ChatOpenAI 客户端。

    配置优先级: 显式参数 > profile > 环境变量 > .env 文件

    Args:
        profile: models.toml 中的 profile 名称。
        api_key: API 密钥。默认从 LLM_API_KEY 读取。
        base_url: API 地址。默认从 LLM_BASE_URL 读取，回退到 https://api.deepseek.com/v1。
        model_name: 模型名称。默认从 LLM_MODEL_NAME 读取，回退到 deepseek-v4-pro。
        max_tokens: 单次输出最大 token 数。默认从 profile 读取；未配置则由服务端决定。
        temperature: 温度参数，默认 0.0。
        timeout: 单次请求超时秒数，默认 60 秒。
    """
    env_values = _read_env_file(_ENV_FILE)
    _load_dotenv()
    profile_name = profile or os.getenv("LLM_PROFILE") or env_values.get("LLM_PROFILE")
    profile_values = _resolve_profile(profile_name, env_values)

    _api_key = (
        api_key
        or profile_values.get("api_key")
        or os.getenv("LLM_API_KEY")
        or env_values.get("LLM_API_KEY")
        or "not-needed"
    )
    _base_url = (
        base_url
        or profile_values.get("base_url")
        or os.getenv("LLM_BASE_URL")
        or env_values.get("LLM_BASE_URL")
        or "https://api.deepseek.com/v1"
    )
    _model_name = (
        model_name
        or profile_values.get("model_name")
        or os.getenv("LLM_MODEL_NAME")
        or env_values.get("LLM_MODEL_NAME")
        or "deepseek-v4-pro"
    )
    _max_tokens = max_tokens or profile_values.get("max_tokens")

    return ChatOpenAI(
        model=_model_name,
        base_url=_base_url,
        api_key=_api_key,  # type: ignore[arg-type]
        temperature=temperature,
        timeout=timeout,
        max_tokens=_max_tokens,  # type: ignore[arg-type]
    )
