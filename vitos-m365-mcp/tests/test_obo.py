import asyncio
from types import SimpleNamespace

from pydantic import SecretStr

import m365_mcp.obo as obo_module
from m365_mcp.obo import OboTokenError, OboTokenService


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        entra_mcp_client_id="mcp-client-id",
        entra_tenant_id="tenant-id",
        entra_mcp_client_secret=SecretStr("mcp-client-secret"),
    )


def test_obo_exchanges_token_m_for_graph_default_scope(monkeypatch) -> None:
    captured = {}

    class FakeApplication:
        def __init__(self, **kwargs) -> None:
            captured["options"] = kwargs

        def acquire_token_on_behalf_of(self, **kwargs):
            captured["request"] = kwargs
            return {"access_token": "token-g"}

    monkeypatch.setattr(obo_module.msal, "ConfidentialClientApplication", FakeApplication)
    service = OboTokenService(_settings())

    token = asyncio.run(service.acquire_graph_token("token-m"))

    assert token == "token-g"
    assert captured["options"] == {
        "client_id": "mcp-client-id",
        "authority": "https://login.microsoftonline.com/tenant-id",
        "client_credential": "mcp-client-secret",
    }
    assert captured["request"] == {
        "user_assertion": "token-m",
        "scopes": ["https://graph.microsoft.com/.default"],
    }
    assert "mcp-client-secret" not in repr(service)


def test_obo_error_is_sanitized(monkeypatch) -> None:
    class FakeApplication:
        def __init__(self, **kwargs) -> None:
            pass

        def acquire_token_on_behalf_of(self, **kwargs):
            return {
                "error": "consent_required",
                "error_description": "sensitive provider detail and token",
            }

    monkeypatch.setattr(obo_module.msal, "ConfidentialClientApplication", FakeApplication)
    service = OboTokenService(_settings())

    try:
        asyncio.run(service.acquire_graph_token("token-m"))
    except OboTokenError as exc:
        assert exc.code == "obo_authorization_required"
        assert "sensitive provider detail" not in str(exc)
    else:
        raise AssertionError("Expected OboTokenError")
