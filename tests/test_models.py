"""LLM profile 配置测试"""

from __future__ import annotations

from pathlib import Path

import pytest

import reasoning_agent.llm.models as models


class DummyChatOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.model_name = kwargs["model"]


@pytest.fixture
def temp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY=env-secret\n", encoding="utf-8")

    models_file = tmp_path / "models.toml"
    models_file.write_text(
        """
[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
model_name = "deepseek-v4-pro"
api_key_env = "LLM_API_KEY"
fallback_profile = "vllm_qwen8b"

[profiles.deepseek_flash]
base_url = "https://api.deepseek.com/v1"
model_name = "deepseek-v4-flash"
api_key_env = "LLM_API_KEY"

[profiles.vllm_qwen8b]
base_url = "http://127.0.0.1:6006/v1"
model_name = "/root/Qwen3-8B"
api_key = "not-needed"
max_tokens = 256
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(models, "_ENV_FILE", env_file)
    monkeypatch.setattr(models, "_MODELS_FILE", models_file)
    monkeypatch.setattr(models, "ChatOpenAI", DummyChatOpenAI)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    monkeypatch.delenv("LLM_PROFILE", raising=False)
    return tmp_path


def test_list_model_profiles(temp_config: Path) -> None:
    assert models.list_model_profiles() == ["deepseek", "deepseek_flash", "vllm_qwen8b"]


def test_init_model_from_profile(temp_config: Path) -> None:
    model = models.init_model(profile="vllm_qwen8b")
    assert model.kwargs["model"] == "/root/Qwen3-8B"
    assert model.kwargs["base_url"] == "http://127.0.0.1:6006/v1"
    assert model.kwargs["api_key"] == "not-needed"
    assert model.kwargs["max_tokens"] == 256


def test_explicit_args_override_profile(temp_config: Path) -> None:
    model = models.init_model(
        profile="vllm_qwen8b",
        model_name="override-model",
        base_url="http://override/v1",
        api_key="override-key",
    )
    assert model.kwargs["model"] == "override-model"
    assert model.kwargs["base_url"] == "http://override/v1"
    assert model.kwargs["api_key"] == "override-key"


def test_profile_api_key_env_reads_env_file(temp_config: Path) -> None:
    model = models.init_model(profile="deepseek")
    assert model.kwargs["api_key"] == "env-secret"
    assert model.kwargs["model"] == "deepseek-v4-pro"


def test_unknown_profile_raises(temp_config: Path) -> None:
    with pytest.raises(ValueError, match="Unknown model profile"):
        models.init_model(profile="missing")


def test_profile_resolves_fallback_profile(temp_config: Path) -> None:
    env_values = models._read_env_file(models._ENV_FILE)
    resolved = models._resolve_profile("deepseek", env_values)
    assert resolved["fallback_profile"] == "vllm_qwen8b"


def test_profile_resolves_max_tokens(temp_config: Path) -> None:
    env_values = models._read_env_file(models._ENV_FILE)
    resolved = models._resolve_profile("vllm_qwen8b", env_values)
    assert resolved["max_tokens"] == 256
