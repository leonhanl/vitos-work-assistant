import asyncio
import socket
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import SecretStr
from pydantic_ai.models.test import TestModel
from starlette.datastructures import Headers

from work_assistant.agent import AgentService
from work_assistant.auth import AuthenticatedRequest, CurrentUser


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize(
    ("portkey_api_key", "expected_portkey_header"),
    [
        (None, None),
        (SecretStr("test-portkey-key"), "test-portkey-key"),
    ],
)
def test_mcp_requests_send_auth_and_optional_portkey_header(
    tmp_path: Path,
    portkey_api_key: SecretStr | None,
    expected_portkey_header: str | None,
) -> None:
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}/mcp"
    test_mcp = FastMCP(
        "test-vitos-m365-mcp",
        stateless_http=True,
        json_response=True,
    )
    seen_authorization: list[str | None] = []
    seen_portkey_api_keys: list[str | None] = []

    class CaptureAuthorization:
        def __init__(self, app) -> None:
            self._app = app

        async def __call__(self, scope, receive, send) -> None:
            if scope["type"] == "http" and scope["path"] == "/mcp":
                headers = Headers(scope=scope)
                seen_authorization.append(headers.get("authorization"))
                seen_portkey_api_keys.append(headers.get("x-portkey-api-key"))
            await self._app(scope, receive, send)

    @test_mcp.tool()
    async def search_sharepoint(query: str) -> list[dict[str, Any]]:
        return [{"name": f"{query}.docx", "rank": 1}]

    skill = tmp_path / "enterprise-knowledge-search"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        """---
name: enterprise-knowledge-search
description: Search enterprise knowledge.
---
Use search_sharepoint.
""",
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        skills_directory=tmp_path,
        m365_mcp_url=endpoint,
        portkey_api_key=portkey_api_key,
    )

    class TokenAcquirer:
        async def acquire_mcp_token(self, token_a: str) -> str:
            await asyncio.sleep(0)
            return token_a.replace("token-a", "token-m")

    service = AgentService(
        settings,
        TokenAcquirer(),
        model=TestModel(call_tools=["search_sharepoint"]),
    )

    async def chat_as(user: str) -> None:
        await service.chat(
            "shared-external-thread",
            "VPN",
            AuthenticatedRequest(
                user=CurrentUser(oid=user, tid="tenant"),
                token_a=f"{user}-token-a",
            ),
        )

    async def run_test() -> None:
        config = uvicorn.Config(
            CaptureAuthorization(test_mcp.streamable_http_app()),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        server_task = asyncio.create_task(server.serve())
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(0.01)
            await asyncio.gather(chat_as("alice"), chat_as("bob"))
        finally:
            server.should_exit = True
            async with asyncio.timeout(5):
                await server_task

    asyncio.run(run_test())

    allowed_headers = {"Bearer alice-token-m", "Bearer bob-token-m"}
    assert seen_authorization
    assert set(seen_authorization) == allowed_headers
    assert seen_authorization.count("Bearer alice-token-m") >= 3
    assert seen_authorization.count("Bearer bob-token-m") >= 3
    assert seen_portkey_api_keys
    assert set(seen_portkey_api_keys) == {expected_portkey_header}
