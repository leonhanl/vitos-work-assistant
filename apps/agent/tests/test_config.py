import pytest
from pydantic import ValidationError

from work_assistant.config import Settings
from work_assistant.llm import create_chat_model


def test_llm_configuration_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_llm_configuration_accepts_openai_compatible_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "tool-model")

    settings = Settings()

    assert settings.llm_model == "tool-model"
    assert settings.llm_api_key.get_secret_value() == "test-key"
    assert str(settings.llm_base_url).startswith("https://provider.example/v1")

    model = create_chat_model(settings)
    assert model.model_name == "tool-model"
    assert model.use_responses_api is False
    assert model.reasoning_effort is None


def test_gpt_5_6_uses_chat_completions_compatible_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.6-terra")

    model = create_chat_model(Settings())

    assert model.use_responses_api is False
    assert model.reasoning_effort == "none"
