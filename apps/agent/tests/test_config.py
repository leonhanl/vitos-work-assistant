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
    monkeypatch.delenv("M365_MCP_URL", raising=False)

    settings = Settings()

    assert settings.llm_model == "tool-model"
    assert settings.llm_api_key.get_secret_value() == "test-key"
    assert str(settings.llm_base_url).startswith("https://provider.example/v1")
    assert str(settings.m365_mcp_url) == "http://127.0.0.1:8001/mcp"

    model = create_chat_model(settings)
    assert model.model_name == "tool-model"
    assert model.use_responses_api is False
    assert model.reasoning_effort is None


def test_m365_streamable_http_endpoint_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "tool-model")
    monkeypatch.setenv("M365_MCP_URL", "http://m365.internal:9000/custom-mcp")

    settings = Settings()

    assert str(settings.m365_mcp_url) == "http://m365.internal:9000/custom-mcp"


def test_gpt_5_6_uses_chat_completions_compatible_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.6-terra")

    model = create_chat_model(Settings())

    assert model.use_responses_api is False
    assert model.reasoning_effort == "none"
