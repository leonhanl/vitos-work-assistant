import asyncio
import socket
from contextlib import closing
from typing import Any

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from work_assistant.config import Settings
from work_assistant.mcp import M365MCPClient


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
            test_mcp.streamable_http_app(),
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
            result = await search.ainvoke({"query": "VPN", "top": 1})
            assert "VPN.docx" in str(result)
        finally:
            await client.close()
            server.should_exit = True
            async with asyncio.timeout(5):
                await server_task

    asyncio.run(run_smoke_test())
