import asyncio
import socket
from contextlib import closing
from typing import Any

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import m365_mcp.server as server_module
from m365_mcp.server import ServerConfig


class MockGraphClient:
    """Graph boundary used to keep the transport smoke test offline."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "MockGraphClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass

    async def search_files(
        self, query: str, top: int = 5
    ) -> list[dict[str, Any]]:
        return [
            {
                "rank": 1,
                "name": "Mock VPN Guide.docx",
                "summary": f"Mock result for {query}",
                "web_url": "https://example.invalid/vpn",
                "drive_id": "mock-drive",
                "item_id": "mock-item",
            }
        ][:top]


@pytest.fixture
def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_server_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)
    monkeypatch.delenv("MCP_PATH", raising=False)

    assert ServerConfig.from_env() == ServerConfig(
        host="127.0.0.1", port=8001, path="/mcp"
    )


def test_streamable_http_lists_and_calls_tools(
    monkeypatch: pytest.MonkeyPatch, free_port: int
) -> None:
    monkeypatch.setattr(server_module, "GraphClient", MockGraphClient)

    async def run_smoke_test() -> None:
        app = server_module.mcp.streamable_http_app()
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=free_port,
            log_level="error",
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        server_task = asyncio.create_task(server.serve())

        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(0.01)

            endpoint = f"http://127.0.0.1:{free_port}/mcp"
            async with streamable_http_client(
                endpoint, terminate_on_close=False
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    listed = await session.list_tools()
                    assert {tool.name for tool in listed.tools} == {
                        "search_sharepoint",
                        "read_document",
                    }

                    called = await session.call_tool(
                        "search_sharepoint", {"query": "VPN", "top": 1}
                    )
                    assert called.isError is False
                    assert "Mock VPN Guide.docx" in str(called)
        finally:
            server.should_exit = True
            async with asyncio.timeout(5):
                await server_task

    asyncio.run(run_smoke_test())
