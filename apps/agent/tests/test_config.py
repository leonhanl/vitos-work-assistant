from pathlib import Path

import pytest
from pydantic import ValidationError

from work_assistant.config import Settings
from work_assistant.llm import create_chat_model_client

TENANT_ID = "11111111-1111-1111-1111-111111111111"
API_CLIENT_ID = "22222222-2222-2222-2222-222222222222"
MCP_CLIENT_ID = "33333333-3333-3333-3333-333333333333"
SKILLS_DIRECTORY = Path(__file__).resolve().parents[1] / "skills"


def _set_required_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "tool-model")
    monkeypatch.setenv("SKILLS_DIRECTORY", str(SKILLS_DIRECTORY))
    monkeypatch.setenv("SKILLS_VERSION", "test-version")
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT_ID)
    monkeypatch.setenv("ENTRA_WORK_ASSISTANT_API_CLIENT_ID", API_CLIENT_ID)
    monkeypatch.setenv("ENTRA_WORK_ASSISTANT_API_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("M365_MCP_URL", "http://127.0.0.1:8001/mcp")
    monkeypatch.setenv("ENTRA_MCP_CLIENT_ID", MCP_CLIENT_ID)


def test_llm_configuration_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_configuration(monkeypatch)
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_llm_configuration_accepts_openai_compatible_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_configuration(monkeypatch)

    settings = Settings()

    assert settings.llm_model == "tool-model"
    assert settings.skills_directory == SKILLS_DIRECTORY
    assert settings.skills_version == "test-version"
    assert settings.llm_api_key.get_secret_value() == "test-key"
    assert str(settings.llm_base_url).startswith("https://provider.example/v1")
    assert str(settings.m365_mcp_url) == "http://127.0.0.1:8001/mcp"
    assert str(settings.entra_tenant_id) == TENANT_ID
    assert str(settings.entra_work_assistant_api_client_id) == API_CLIENT_ID
    assert (
        settings.entra_work_assistant_api_client_secret.get_secret_value()
        == "test-secret"
    )
    assert settings.entra_required_scope == "access_as_user"
    assert settings.mcp_scope == f"api://{MCP_CLIENT_ID}/access_as_user"

    model = create_chat_model_client(settings)
    assert model.model_name == "tool-model"
    assert model.settings == {"timeout": 60.0}


def test_m365_streamable_http_endpoint_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_configuration(monkeypatch)
    monkeypatch.setenv("M365_MCP_URL", "http://m365.internal:9000/custom-mcp")

    settings = Settings()

    assert str(settings.m365_mcp_url) == "http://m365.internal:9000/custom-mcp"


def test_portkey_api_key_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_configuration(monkeypatch)
    monkeypatch.delenv("PORTKEY_API_KEY", raising=False)

    assert Settings().portkey_api_key is None


def test_portkey_api_key_is_loaded_as_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_configuration(monkeypatch)
    monkeypatch.setenv("PORTKEY_API_KEY", "test-portkey-key")

    api_key = Settings().portkey_api_key

    assert api_key is not None
    assert api_key.get_secret_value() == "test-portkey-key"


def test_skills_artifact_configuration_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_configuration(monkeypatch)
    monkeypatch.delenv("SKILLS_DIRECTORY")
    monkeypatch.delenv("SKILLS_VERSION")

    with pytest.raises(ValidationError):
        Settings()


def test_skills_artifact_must_contain_a_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_configuration(monkeypatch)
    monkeypatch.setenv("SKILLS_DIRECTORY", str(tmp_path))

    with pytest.raises(ValidationError):
        Settings()


def test_m365_streamable_http_endpoint_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_configuration(monkeypatch)
    monkeypatch.delenv("M365_MCP_URL")

    with pytest.raises(ValidationError):
        Settings()


def test_mcp_scope_can_override_the_default_application_id_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_configuration(monkeypatch)
    monkeypatch.setenv(
        "ENTRA_MCP_SCOPE",
        "api://m365.internal/access_as_user",
    )

    assert Settings().mcp_scope == "api://m365.internal/access_as_user"


def test_gpt_5_6_uses_chat_completions_compatible_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_configuration(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.6-terra")

    model = create_chat_model_client(Settings())

    assert model.settings == {
        "timeout": 60.0,
        "openai_reasoning_effort": "none",
    }


def test_entra_configuration_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_configuration(monkeypatch)
    monkeypatch.delenv("ENTRA_TENANT_ID")
    monkeypatch.delenv("ENTRA_WORK_ASSISTANT_API_CLIENT_ID")
    monkeypatch.delenv("ENTRA_WORK_ASSISTANT_API_CLIENT_SECRET")
    monkeypatch.delenv("ENTRA_MCP_CLIENT_ID")

    with pytest.raises(ValidationError):
        Settings()
