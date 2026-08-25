import asyncio
import logging
from types import SimpleNamespace

import jwt
from pydantic import SecretStr

import work_assistant.obo as obo_module
from work_assistant.obo import OboTokenError, OboTokenService


def _settings(
    scope: str = "api://mcp-client-id/access_as_user",
    *,
    log_token_claims: bool = False,
):
    return SimpleNamespace(
        entra_work_assistant_api_client_id="agent-api-client-id",
        entra_tenant_id="tenant-id",
        entra_work_assistant_api_client_secret=SecretStr("client-secret"),
        mcp_scope=scope,
        entra_log_mcp_token_claims=log_token_claims,
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


def test_obo_service_logs_decoded_token_m_claims_when_enabled(
    monkeypatch,
    caplog,
) -> None:
    token_m = jwt.encode(
        {
            "aud": "mcp-client-id",
            "azp": "agent-api-client-id",
            "oid": "alice-id",
            "scp": "access_as_user",
            "tid": "tenant-id",
            "ver": "2.0",
        },
        "test-signing-secret-with-at-least-32-bytes",
        algorithm="HS256",
        headers={"kid": "test-key-id"},
    )

    class FakeApplication:
        def __init__(self, **kwargs) -> None:
            pass

        def acquire_token_on_behalf_of(self, **kwargs):
            return {"access_token": token_m}

    monkeypatch.setattr(
        obo_module.msal,
        "ConfidentialClientApplication",
        FakeApplication,
    )
    service = OboTokenService(_settings(log_token_claims=True))

    with caplog.at_level(logging.INFO, logger="work_assistant.obo"):
        assert asyncio.run(service.acquire_mcp_token("token-a")) == token_m

    assert '"aud":"mcp-client-id"' in caplog.text
    assert '"azp":"agent-api-client-id"' in caplog.text
    assert '"oid":"alice-id"' in caplog.text
    assert '"scp":"access_as_user"' in caplog.text
    assert '"kid":"test-key-id"' in caplog.text
    assert token_m not in caplog.text
    assert "test-signing-secret-with-at-least-32-bytes" not in caplog.text


def test_obo_service_does_not_log_token_m_claims_by_default(
    monkeypatch,
    caplog,
) -> None:
    token_m = jwt.encode(
        {"aud": "mcp-client-id", "oid": "alice-id"},
        "test-signing-secret-with-at-least-32-bytes",
        algorithm="HS256",
    )

    class FakeApplication:
        def __init__(self, **kwargs) -> None:
            pass

        def acquire_token_on_behalf_of(self, **kwargs):
            return {"access_token": token_m}

    monkeypatch.setattr(
        obo_module.msal,
        "ConfidentialClientApplication",
        FakeApplication,
    )
    service = OboTokenService(_settings())

    with caplog.at_level(logging.INFO, logger="work_assistant.obo"):
        assert asyncio.run(service.acquire_mcp_token("token-a")) == token_m

    assert "MCP Token M unverified diagnostic" not in caplog.text
    assert token_m not in caplog.text
