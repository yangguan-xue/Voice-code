"""LLM profile 配置测试"""

from __future__ import annotations

from pathlib import Path

import pytest

import voice_code.llm.models as models


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
fallback_profile = "local_openai"
context_window = 1000000

[profiles.deepseek_flash]
base_url = "https://api.deepseek.com/v1"
model_name = "deepseek-v4-flash"
api_key_env = "LLM_API_KEY"

[profiles.local_openai]
base_url = "http://127.0.0.1:8000/v1"
model_name = "local-model"
api_key_env = "LOCAL_LLM_API_KEY"
max_tokens = 2048
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
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "not-needed")
    models.set_active_profile(None)
    return tmp_path


def test_list_model_profiles(temp_config: Path) -> None:
    assert models.list_model_profiles() == ["deepseek", "deepseek_flash", "local_openai"]


def test_init_model_from_profile(temp_config: Path) -> None:
    model = models.init_model(profile="local_openai")
    assert model.kwargs["model"] == "local-model"
    assert model.kwargs["base_url"] == "http://127.0.0.1:8000/v1"
    assert model.kwargs["api_key"] == "not-needed"
    assert model.kwargs["max_tokens"] == 2048
    assert model.kwargs["http_client"]._trust_env is False
    assert model.kwargs["http_async_client"]._trust_env is False


def test_remote_profile_keeps_default_http_client(temp_config: Path) -> None:
    model = models.init_model(profile="deepseek")
    assert "http_client" not in model.kwargs
    assert "http_async_client" not in model.kwargs


def test_explicit_args_override_profile(temp_config: Path) -> None:
    model = models.init_model(
        profile="local_openai",
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
    assert resolved["fallback_profile"] == "local_openai"


def test_profile_resolves_max_tokens(temp_config: Path) -> None:
    env_values = models._read_env_file(models._ENV_FILE)
    resolved = models._resolve_profile("local_openai", env_values)
    assert resolved["max_tokens"] == 2048


def test_context_window_uses_active_profile_from_environment(
    temp_config: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_PROFILE", "deepseek")

    profile = models.resolve_profile_name()
    models.set_active_profile(profile)

    assert profile == "deepseek"
    assert models.get_active_context_window(128_000) == 1_000_000


def test_context_window_uses_active_profile_from_env_file(temp_config: Path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROFILE", raising=False)
    models._ENV_FILE.write_text(
        "LLM_API_KEY=env-secret\nLLM_PROFILE=deepseek\n",
        encoding="utf-8",
    )

    profile = models.resolve_profile_name()
    models.set_active_profile(profile)

    assert profile == "deepseek"
    assert models.get_active_context_window(128_000) == 1_000_000


def test_plaintext_profile_api_key_is_rejected(temp_config: Path) -> None:
    models._MODELS_FILE.write_text(
        """
[profiles.leaky]
base_url = "https://api.example.test/v1"
model_name = "leaky-model"
api_key = "sk-plaintext"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Plaintext api_key.*api_key_env"):
        models.init_model(profile="leaky")


def test_list_model_profiles_rejects_plaintext_api_key(temp_config: Path) -> None:
    models._MODELS_FILE.write_text(
        """
[profiles.leaky]
base_url = "https://api.example.test/v1"
model_name = "leaky-model"
api_key = "sk-plaintext"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Move the secret to an environment variable"):
        models.list_model_profiles()
