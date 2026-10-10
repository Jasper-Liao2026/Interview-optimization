"""M3 model settings are independent and reject unsupported providers."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_m3_defaults_preserve_offline_operation():
    configured = Settings()
    assert configured.llm_provider == "stub"
    assert configured.embedding_provider == "stub"
    assert configured.vision_provider is None
    assert configured.vision_api_key is None
    assert configured.embedding_api_key is None


def test_embedding_and_vision_settings_load_independently_from_environment(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDER", "openai-compatible")
    monkeypatch.setenv("VISION_API_KEY", "vision-test")
    monkeypatch.setenv("VISION_MODEL", "vision-test-model")
    monkeypatch.setenv("VISION_BASE_URL", "https://vision.invalid/v1")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai-compatible")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-test")
    monkeypatch.setenv("EMBEDDING_MODEL", "embedding-test-model")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embedding.invalid/v1")
    configured = Settings()
    assert configured.llm_provider == "stub"
    assert configured.llm_api_key is None
    assert configured.vision_api_key == "vision-test"
    assert configured.vision_model == "vision-test-model"
    assert configured.vision_base_url == "https://vision.invalid/v1"
    assert configured.embedding_provider == "openai-compatible"
    assert configured.embedding_api_key == "embedding-test"
    assert configured.embedding_model == "embedding-test-model"
    assert configured.embedding_base_url == "https://embedding.invalid/v1"


@pytest.mark.parametrize(
    "updates",
    [
        {"embedding_provider": "unknown"},
        {"vision_provider": "unknown"},
        {"embedding_timeout_s": 0},
        {"embedding_timeout_s": -1},
    ],
)
def test_invalid_embedding_or_vision_configuration_is_rejected(updates):
    with pytest.raises(ValidationError):
        Settings(**updates)
