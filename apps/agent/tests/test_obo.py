import asyncio
import logging
from types import SimpleNamespace

from pydantic import SecretStr

import work_assistant.obo as obo_module
from work_assistant.auth import AuthenticatedRequest, CurrentUser
from work_assistant.obo import OboTokenError, OboTokenService, RequestAuthContext


class CountingTokenAcquirer:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire_graph_token(self, token_a: str) -> str:
        self.calls += 1
        await asyncio.sleep(0)
        return f"graph:{token_a}"


def test_graph_token_is_acquired_lazily_once_for_concurrent_tool_calls(
    caplog,
) -> None:
    async def run_test() -> None:
        acquirer = CountingTokenAcquirer()
        context = RequestAuthContext(
            user=CurrentUser(oid="alice", tid="tenant"),
            token_a="alice-token-a",
            token_acquirer=acquirer,
        )

        assert acquirer.calls == 0
        tokens = await asyncio.gather(
            context.get_graph_token(),
            context.get_graph_token(),
            context.get_graph_token(),
        )

        assert tokens == ["graph:alice-token-a"] * 3
        assert acquirer.calls == 1

    with caplog.at_level(logging.INFO, logger="work_assistant.obo"):
        asyncio.run(run_test())

    messages = [record.getMessage() for record in caplog.records]
    assert sum("OBO token exchange started" in message for message in messages) == 1
    assert sum("OBO token exchange succeeded" in message for message in messages) == 1
    assert any("oid=alice tid=tenant" in message for message in messages)
    assert all("alice-token-a" not in message for message in messages)
    assert all("graph:alice-token-a" not in message for message in messages)


def test_request_contexts_do_not_share_graph_tokens() -> None:
    async def run_test() -> None:
        acquirer = CountingTokenAcquirer()
        alice = RequestAuthContext(
            user=CurrentUser(oid="alice", tid="tenant"),
            token_a="alice-token-a",
            token_acquirer=acquirer,
        )
        bob = RequestAuthContext(
            user=CurrentUser(oid="bob", tid="tenant"),
            token_a="bob-token-a",
            token_acquirer=acquirer,
        )

        assert await asyncio.gather(
            alice.get_graph_token(), bob.get_graph_token()
        ) == ["graph:alice-token-a", "graph:bob-token-a"]
        assert acquirer.calls == 2

    asyncio.run(run_test())


def test_request_credentials_are_not_exposed_by_repr() -> None:
    acquirer = CountingTokenAcquirer()
    user = CurrentUser(oid="alice", tid="tenant")
    authenticated = AuthenticatedRequest(user=user, token_a="secret-token-a")
    context = RequestAuthContext(
        user=user,
        token_a="secret-token-a",
        token_acquirer=acquirer,
    )

    assert "secret-token-a" not in repr(authenticated)
    assert "secret-token-a" not in repr(context)


def test_obo_service_uses_graph_default_scope(monkeypatch) -> None:
    captured = {}

    class FakeApplication:
        def __init__(self, **kwargs) -> None:
            captured["options"] = kwargs

        def acquire_token_on_behalf_of(self, **kwargs):
            captured["request"] = kwargs
            return {"access_token": "token-b"}

    monkeypatch.setattr(obo_module.msal, "ConfidentialClientApplication", FakeApplication)
    settings = SimpleNamespace(
        entra_work_assistant_api_client_id="api-client-id",
        entra_tenant_id="tenant-id",
        entra_work_assistant_api_client_secret=SecretStr("client-secret"),
    )

    service = OboTokenService(settings)
    token = asyncio.run(service.acquire_graph_token("token-a"))

    assert token == "token-b"
    assert captured["request"] == {
        "user_assertion": "token-a",
        "scopes": ["https://graph.microsoft.com/.default"],
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

    monkeypatch.setattr(obo_module.msal, "ConfidentialClientApplication", FakeApplication)
    settings = SimpleNamespace(
        entra_work_assistant_api_client_id="api-client-id",
        entra_tenant_id="tenant-id",
        entra_work_assistant_api_client_secret=SecretStr("client-secret"),
    )
    service = OboTokenService(settings)

    try:
        asyncio.run(service.acquire_graph_token("token-a"))
    except OboTokenError as exc:
        assert exc.code == "obo_authorization_required"
        assert "sensitive provider detail" not in str(exc)
    else:
        raise AssertionError("Expected OboTokenError")
