import asyncio
import socket
from contextlib import closing
from typing import Any

import pytest
import uvicorn
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from starlette.datastructures import Headers

from work_assistant.auth import CurrentUser
from work_assistant.config import Settings
from work_assistant.mcp import (
    M365MCPClient,
    _inject_graph_token,
    bind_request_auth,
)
from work_assistant.obo import RequestAuthContext


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _settings(monkeypatch: pytest.MonkeyPatch, endpoint: str) -> Settings:
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "tool-model")
    monkeypatch.setenv("ENTRA_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv(
        "ENTRA_WORK_ASSISTANT_API_CLIENT_ID",
        "22222222-2222-2222-2222-222222222222",
    )
    monkeypatch.setenv("ENTRA_WORK_ASSISTANT_API_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("M365_MCP_URL", endpoint)
    return Settings()


def test_agent_connects_to_streamable_http_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}/mcp"
    test_mcp = FastMCP(
        "test-m365-mcp-http",
        stateless_http=True,
        json_response=True,
    )
    seen_authorization: list[str | None] = []

    class CaptureAuthorization:
        def __init__(self, app) -> None:
            self._app = app

        async def __call__(self, scope, receive, send) -> None:
            if scope["type"] == "http":
                seen_authorization.append(Headers(scope=scope).get("authorization"))
            await self._app(scope, receive, send)

    @test_mcp.tool()
    async def search_sharepoint(
        query: str, top: int = 5
    ) -> list[dict[str, Any]]:
        return [{"name": f"{query}.docx", "rank": 1}][:top]

    @test_mcp.tool()
    async def read_document(drive_id: str, item_id: str) -> dict[str, str]:
        return {"name": "Mock.docx", "content": f"{drive_id}:{item_id}"}

    async def run_smoke_test() -> None:
        config = uvicorn.Config(
            CaptureAuthorization(test_mcp.streamable_http_app()),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        server_task = asyncio.create_task(server.serve())
        client = M365MCPClient(_settings(monkeypatch, endpoint))

        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(0.01)

            tools = await client.connect()
            assert {tool.name for tool in tools} == {
                "search_sharepoint",
                "read_document",
            }

            search = next(tool for tool in tools if tool.name == "search_sharepoint")
            class FakeTokenAcquirer:
                def __init__(self) -> None:
                    self.calls = 0

                async def acquire_graph_token(self, token_a: str) -> str:
                    self.calls += 1
                    assert token_a == "token-a"
                    return "token-b"

            token_acquirer = FakeTokenAcquirer()
            auth_context = RequestAuthContext(
                user=CurrentUser(oid="alice", tid="tenant"),
                token_a="token-a",
                token_acquirer=token_acquirer,
            )
            with bind_request_auth(auth_context):
                result = await search.ainvoke({"query": "VPN", "top": 1})
                second_result = await search.ainvoke({"query": "Policy", "top": 1})
            assert "VPN.docx" in str(result)
            assert "Policy.docx" in str(second_result)
            assert token_acquirer.calls == 1
            assert "Bearer token-b" in seen_authorization
        finally:
            await client.close()
            server.should_exit = True
            async with asyncio.timeout(5):
                await server_task

    asyncio.run(run_smoke_test())


def test_concurrent_requests_inject_their_own_graph_token() -> None:
    seen: dict[str, str] = {}

    class TokenAcquirer:
        async def acquire_graph_token(self, token_a: str) -> str:
            await asyncio.sleep(0)
            return token_a.replace("token-a", "token-b")

    async def handler(request: MCPToolCallRequest) -> CallToolResult:
        await asyncio.sleep(0)
        assert request.headers is not None
        seen[request.args["user"]] = request.headers["Authorization"]
        return CallToolResult(content=[])

    async def invoke(user: str) -> None:
        context = RequestAuthContext(
            user=CurrentUser(oid=user, tid="tenant"),
            token_a=f"{user}-token-a",
            token_acquirer=TokenAcquirer(),
        )
        request = MCPToolCallRequest(
            name="search_sharepoint",
            args={"user": user},
            server_name="m365-mcp-http",
        )
        with bind_request_auth(context):
            await _inject_graph_token(request, handler)

    async def run_test() -> None:
        await asyncio.gather(invoke("alice"), invoke("bob"))

    asyncio.run(run_test())

    assert seen == {
        "alice": "Bearer alice-token-b",
        "bob": "Bearer bob-token-b",
    }
