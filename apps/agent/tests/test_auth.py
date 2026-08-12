from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import work_assistant.auth as auth_module
from work_assistant.app import create_app
from work_assistant.auth import EntraTokenValidator, ValidationContext
from work_assistant.models import ChatResponse

TENANT_ID = "11111111-1111-1111-1111-111111111111"
API_CLIENT_ID = "22222222-2222-2222-2222-222222222222"
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
GRAPH_AUDIENCE = "00000003-0000-0000-c000-000000000000"


class FakeAgent:
    async def chat(self, thread_id: str, message: str) -> ChatResponse:
        return ChatResponse(thread_id=thread_id, answer=f"answer: {message}")


class StaticKeyValidator(EntraTokenValidator):
    def __init__(self, public_key: Any) -> None:
        super().__init__(TENANT_ID, API_CLIENT_ID, "access_as_user")
        self._public_key = public_key

    async def _resolve_validation_context(self, token: str) -> ValidationContext:
        return ValidationContext(ISSUER, self._public_key)


@pytest.fixture
def signing_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def validator(signing_key: Any) -> StaticKeyValidator:
    return StaticKeyValidator(signing_key.public_key())


def _token(signing_key: Any, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "aud": API_CLIENT_ID,
        "iss": ISSUER,
        "exp": now + timedelta(minutes=5),
        "nbf": now - timedelta(seconds=5),
        "iat": now,
        "oid": "alice-oid",
        "tid": TENANT_ID,
        "preferred_username": "alice@example.com",
        "scp": "access_as_user",
        "ver": "2.0",
    }
    claims.update(overrides)
    return jwt.encode(
        claims,
        signing_key,
        algorithm="RS256",
        headers={"kid": "offline-test-key"},
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    validator: StaticKeyValidator,
) -> TestClient:
    monkeypatch.setattr(auth_module, "get_token_validator", lambda: validator)
    return TestClient(create_app(FakeAgent()))


def test_missing_bearer_token_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    validator: StaticKeyValidator,
) -> None:
    with _client(monkeypatch, validator) as client:
        response = client.get("/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_malformed_token_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    validator: StaticKeyValidator,
) -> None:
    with _client(monkeypatch, validator) as client:
        response = client.get(
            "/me",
            headers={"Authorization": "Bearer not-a-jwt"},
        )

    assert response.status_code == 401


def test_invalid_signature_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    validator: StaticKeyValidator,
) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(other_key)

    with _client(monkeypatch, validator) as client:
        response = client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(minutes=2)},
        {"nbf": datetime.now(UTC) + timedelta(minutes=2)},
        {"aud": GRAPH_AUDIENCE},
        {"iss": "https://login.microsoftonline.com/wrong-tenant/v2.0"},
        {"tid": "33333333-3333-3333-3333-333333333333"},
        {"ver": "1.0"},
    ],
    ids=[
        "expired",
        "not-yet-valid",
        "wrong-audience",
        "wrong-issuer",
        "wrong-tenant",
        "wrong-token-version",
    ],
)
def test_invalid_token_claims_return_401(
    monkeypatch: pytest.MonkeyPatch,
    validator: StaticKeyValidator,
    signing_key: Any,
    claim_overrides: dict[str, Any],
) -> None:
    token = _token(signing_key, **claim_overrides)

    with _client(monkeypatch, validator) as client:
        response = client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 401


def test_missing_required_scope_returns_403(
    monkeypatch: pytest.MonkeyPatch,
    validator: StaticKeyValidator,
    signing_key: Any,
) -> None:
    token = _token(signing_key, scp="profile another_scope")

    with _client(monkeypatch, validator) as client:
        response = client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert "insufficient_scope" in response.headers["www-authenticate"]


@pytest.mark.parametrize(
    ("oid", "username"),
    [
        ("alice-oid", "alice@example.com"),
        ("bob-oid", "bob@example.com"),
    ],
)
def test_valid_user_can_call_me_and_chat(
    monkeypatch: pytest.MonkeyPatch,
    validator: StaticKeyValidator,
    signing_key: Any,
    oid: str,
    username: str,
) -> None:
    token = _token(
        signing_key,
        oid=oid,
        preferred_username=username,
    )
    headers = {"Authorization": f"Bearer {token}"}

    with _client(monkeypatch, validator) as client:
        me_response = client.get("/me", headers=headers)
        chat_response = client.post(
            "/chat",
            headers=headers,
            json={"message": "hello"},
        )

    assert me_response.status_code == 200
    assert me_response.json() == {
        "oid": oid,
        "tid": TENANT_ID,
        "username": username,
    }
    assert chat_response.status_code == 200
    assert chat_response.json()["answer"] == "answer: hello"
