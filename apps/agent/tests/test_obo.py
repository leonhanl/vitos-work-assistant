import asyncio
from types import SimpleNamespace

from pydantic import SecretStr

import work_assistant.obo as obo_module
from work_assistant.obo import OboTokenError, OboTokenService


def _settings(scope: str = "api://mcp-client-id/access_as_user"):
    return SimpleNamespace(
        entra_work_assistant_api_client_id="agent-api-client-id",
        entra_tenant_id="tenant-id",
        entra_work_assistant_api_client_secret=SecretStr("client-secret"),
        mcp_scope=scope,
    )


def test_obo_service_exchanges_token_a_for_mcp_token_m(monkeypatch) -> None:
    captured = {}

    class FakeApplication:
        def __init__(self, **kwargs) -> None:
            captured["options"] = kwargs

        def acquire_token_on_behalf_of(self, **kwargs):
            captured["request"] = kwargs
            return {"access_token": "token-m"}

    monkeypatch.setattr(
        obo_module.msal,
        "ConfidentialClientApplication",
        FakeApplication,
    )
    service = OboTokenService(_settings())

    token = asyncio.run(service.acquire_mcp_token("token-a"))

    assert token == "token-m"
    assert captured["options"] == {
        "client_id": "agent-api-client-id",
        "authority": "https://login.microsoftonline.com/tenant-id",
        "client_credential": "client-secret",
    }
    assert captured["request"] == {
        "user_assertion": "token-a",
        "scopes": ["api://mcp-client-id/access_as_user"],
    }
    assert "client-secret" not in repr(service)


def test_obo_error_does_not_expose_provider_description(monkeypatch) -> None:
    class FakeApplication:
        def __init__(self, **kwargs) -> None:
            pass

        def acquire_token_on_behalf_of(self, **kwargs):
            return {
                "error": "consent_required",
                "error_description": "sensitive provider detail",
            }

    monkeypatch.setattr(
        obo_module.msal,
        "ConfidentialClientApplication",
        FakeApplication,
    )
    service = OboTokenService(_settings())

    try:
        asyncio.run(service.acquire_mcp_token("token-a"))
    except OboTokenError as exc:
        assert exc.code == "obo_authorization_required"
        assert "sensitive provider detail" not in str(exc)
    else:
        raise AssertionError("Expected OboTokenError")


def test_blank_user_assertion_is_rejected_before_msal() -> None:
    service = OboTokenService(_settings())

    try:
        asyncio.run(service.acquire_mcp_token("  "))
    except OboTokenError as exc:
        assert exc.code == "invalid_user_assertion"
    else:
        raise AssertionError("Expected OboTokenError")
