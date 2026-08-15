from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from m365_mcp.auth import EntraTokenVerifier, ValidationContext

TENANT_ID = "11111111-1111-1111-1111-111111111111"
MCP_CLIENT_ID = "22222222-2222-2222-2222-222222222222"
CALLER_CLIENT_ID = "33333333-3333-3333-3333-333333333333"
GRAPH_AUDIENCE = "00000003-0000-0000-c000-000000000000"
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
FULL_SCOPE = f"api://{MCP_CLIENT_ID}/access_as_user"


class StaticKeyVerifier(EntraTokenVerifier):
    def __init__(self, public_key: Any) -> None:
        super().__init__(TENANT_ID, MCP_CLIENT_ID, "access_as_user", FULL_SCOPE)
        self._public_key = public_key

    async def _resolve_validation_context(self, token: str) -> ValidationContext:
        return ValidationContext(ISSUER, self._public_key)


@pytest.fixture
def signing_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verifier(signing_key: Any) -> StaticKeyVerifier:
    return StaticKeyVerifier(signing_key.public_key())


def _token(signing_key: Any, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "aud": MCP_CLIENT_ID,
        "azp": CALLER_CLIENT_ID,
        "exp": now + timedelta(minutes=5),
        "iat": now,
        "iss": ISSUER,
        "nbf": now - timedelta(seconds=5),
        "oid": "alice-oid",
        "scp": "access_as_user",
        "tid": TENANT_ID,
        "ver": "2.0",
    }
    claims.update(overrides)
    return jwt.encode(
        claims,
        signing_key,
        algorithm="RS256",
        headers={"kid": "offline-test-key"},
    )


def test_valid_token_m_returns_mcp_access_context(
    verifier: StaticKeyVerifier,
    signing_key: Any,
) -> None:
    token = _token(signing_key)

    result = asyncio.run(verifier.verify_token(token))

    assert result is not None
    assert result.token == token
    assert result.client_id == CALLER_CLIENT_ID
    assert result.subject == "alice-oid"
    assert result.resource == MCP_CLIENT_ID
    assert result.scopes == ["access_as_user", FULL_SCOPE]
    assert result.claims == {
        "iss": ISSUER,
        "tid": TENANT_ID,
        "oid": "alice-oid",
        "azp": CALLER_CLIENT_ID,
    }


def test_valid_token_without_scope_does_not_gain_advertised_scope(
    verifier: StaticKeyVerifier,
    signing_key: Any,
) -> None:
    result = asyncio.run(
        verifier.verify_token(_token(signing_key, scp="profile another_scope"))
    )

    assert result is not None
    assert result.scopes == ["profile", "another_scope"]
    assert FULL_SCOPE not in result.scopes


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(minutes=2)},
        {"nbf": datetime.now(UTC) + timedelta(minutes=2)},
        {"aud": GRAPH_AUDIENCE},
        {"iss": "https://login.microsoftonline.com/wrong/v2.0"},
        {"tid": "44444444-4444-4444-4444-444444444444"},
        {"ver": "1.0"},
        {"azp": ""},
        {"oid": ""},
    ],
    ids=[
        "expired",
        "not-yet-valid",
        "wrong-audience",
        "wrong-issuer",
        "wrong-tenant",
        "wrong-version",
        "empty-authorized-party",
        "empty-user-object-id",
    ],
)
def test_invalid_token_m_is_rejected(
    verifier: StaticKeyVerifier,
    signing_key: Any,
    claim_overrides: dict[str, Any],
) -> None:
    result = asyncio.run(
        verifier.verify_token(_token(signing_key, **claim_overrides))
    )

    assert result is None


def test_invalid_signature_is_rejected(verifier: StaticKeyVerifier) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    assert asyncio.run(verifier.verify_token(_token(other_key))) is None
