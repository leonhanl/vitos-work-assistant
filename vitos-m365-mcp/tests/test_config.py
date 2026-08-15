from uuid import UUID

import pytest
from pydantic import ValidationError

from m365_mcp.config import Settings

TENANT_ID = "11111111-1111-1111-1111-111111111111"
MCP_CLIENT_ID = "22222222-2222-2222-2222-222222222222"


def _required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT_ID)
    monkeypatch.setenv("ENTRA_MCP_CLIENT_ID", MCP_CLIENT_ID)
    monkeypatch.setenv("ENTRA_MCP_CLIENT_SECRET", "test-secret")


def test_settings_defaults_and_derived_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    _required_env(monkeypatch)
    for name in (
        "MCP_HOST",
        "MCP_PORT",
        "MCP_PATH",
        "MCP_RESOURCE_URL",
        "ENTRA_REQUIRED_SCOPE",
        "ENTRA_MCP_SCOPE",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.entra_tenant_id == UUID(TENANT_ID)
    assert settings.entra_mcp_client_id == UUID(MCP_CLIENT_ID)
    assert settings.mcp_host == "127.0.0.1"
    assert settings.mcp_port == 8001
    assert settings.mcp_path == "/mcp"
    assert str(settings.mcp_resource_url) == "http://127.0.0.1:8001/mcp"
    assert settings.authorization_scope == f"api://{MCP_CLIENT_ID}/access_as_user"
    assert settings.issuer_url == (
        f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
    )


def test_explicit_application_scope_is_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _required_env(monkeypatch)
    monkeypatch.setenv("ENTRA_MCP_SCOPE", "api://mcp.internal/access_as_user")

    assert Settings().authorization_scope == "api://mcp.internal/access_as_user"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MCP_PORT", "70000"),
        ("MCP_PATH", "mcp"),
        ("ENTRA_REQUIRED_SCOPE", "two scopes"),
        ("ENTRA_REQUIRED_SCOPE", "api://full/scope"),
        ("ENTRA_MCP_SCOPE", "two scopes"),
        ("ENTRA_MCP_CLIENT_SECRET", " "),
    ],
)
def test_invalid_settings_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    _required_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()
