import asyncio

import pytest

from m365_mcp.auth import AuthenticationError, GraphTokenMiddleware, get_access_token


def test_missing_request_token_is_rejected() -> None:
    with pytest.raises(AuthenticationError, match="Graph access token"):
        get_access_token()


def test_middleware_binds_bearer_token_only_for_the_request() -> None:
    seen: list[str] = []

    async def inner_app(scope, receive, send) -> None:
        seen.append(get_access_token())

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(b"authorization", b"Bearer token-b")],
    }

    async def run_test() -> None:
        async def receive():
            return {"type": "http.disconnect"}

        async def send(message) -> None:
            pass

        await GraphTokenMiddleware(inner_app)(scope, receive, send)

    asyncio.run(run_test())

    assert seen == ["token-b"]
    with pytest.raises(AuthenticationError):
        get_access_token()
