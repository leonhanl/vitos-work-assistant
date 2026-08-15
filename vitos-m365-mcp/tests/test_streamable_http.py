import asyncio
import socket
import time
from contextlib import closing
from typing import Any

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from pydantic import SecretStr

import m365_mcp.server as server_module
from m365_mcp.config import Settings

TENANT_ID = "11111111-1111-1111-1111-111111111111"
MCP_CLIENT_ID = "22222222-2222-2222-2222-222222222222"
FULL_SCOPE = f"api://{MCP_CLIENT_ID}/access_as_user"


class TestTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token == "invalid-token-m":
            return None
        scopes = [] if token == "no-scope-token-m" else [FULL_SCOPE]
        return AccessToken(
            token=token,
            client_id="test-client",
            scopes=scopes,
            expires_at=int(time.time()) + 300,
            resource=MCP_CLIENT_ID,
            subject="alice-oid",
            claims={"iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"},
        )


class RecordingTokenAcquirer:
    seen_token_m: list[str] = []

    async def acquire_graph_token(self, token_m: str) -> str:
        self.seen_token_m.append(token_m)
        return f"token-g-for:{token_m}"


class MockGraphClient:
    seen_token_g: list[str] = []

    def __init__(self, token_provider, **_kwargs: object) -> None:
        self._token_provider = token_provider

    async def __aenter__(self) -> "MockGraphClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass

    async def search_files(
        self, query: str, top: int = 5
    ) -> list[dict[str, Any]]:
        self.seen_token_g.append(self._token_provider())
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


def _settings(port: int) -> Settings:
    endpoint = f"http://127.0.0.1:{port}/mcp"
    return Settings(
        mcp_port=port,
        mcp_resource_url=endpoint,
        entra_tenant_id=TENANT_ID,
        entra_mcp_client_id=MCP_CLIENT_ID,
        entra_mcp_client_secret=SecretStr("test-secret"),
    )


def test_authenticated_streamable_http_and_auth_discovery(
    monkeypatch: pytest.MonkeyPatch,
    free_port: int,
) -> None:
    monkeypatch.setattr(server_module, "GraphClient", MockGraphClient)
    RecordingTokenAcquirer.seen_token_m.clear()
    MockGraphClient.seen_token_g.clear()

    async def run_smoke_test() -> None:
        settings = _settings(free_port)
        app = server_module.create_http_app(
            settings,
            token_verifier=TestTokenVerifier(),
            token_acquirer=RecordingTokenAcquirer(),
        )
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
            metadata_url = (
                f"http://127.0.0.1:{free_port}"
                "/.well-known/oauth-protected-resource/mcp"
            )
            async with httpx.AsyncClient() as probe_client:
                health = await probe_client.get(
                    f"http://127.0.0.1:{free_port}/health"
                )
                assert health.status_code == 200
                assert health.json() == {"status": "ok"}

                unauthorized = await probe_client.post(endpoint)
                assert unauthorized.status_code == 401
                assert metadata_url in unauthorized.headers["www-authenticate"]

                invalid = await probe_client.post(
                    endpoint,
                    headers={"Authorization": "Bearer invalid-token-m"},
                )
                assert invalid.status_code == 401

                forbidden = await probe_client.post(
                    endpoint,
                    headers={"Authorization": "Bearer no-scope-token-m"},
                )
                assert forbidden.status_code == 403
                assert "insufficient_scope" in forbidden.headers["www-authenticate"]

                metadata = await probe_client.get(metadata_url)
                assert metadata.status_code == 200
                assert metadata.json() == {
                    "resource": endpoint,
                    "authorization_servers": [settings.issuer_url],
                    "scopes_supported": [FULL_SCOPE],
                    "bearer_methods_supported": ["header"],
                }

            async with httpx.AsyncClient(
                headers={"Authorization": "Bearer alice-token-m"}
            ) as http_client:
                async with streamable_http_client(
                    endpoint,
                    http_client=http_client,
                    terminate_on_close=False,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()

                        listed = await session.list_tools()
                        assert {tool.name for tool in listed.tools} == {
                            "search_sharepoint",
                            "read_document",
                        }
                        assert RecordingTokenAcquirer.seen_token_m == []

                        called = await session.call_tool(
                            "search_sharepoint", {"query": "VPN", "top": 1}
                        )
                        assert called.isError is False
                        assert "Mock VPN Guide.docx" in str(called)
                        assert RecordingTokenAcquirer.seen_token_m == ["alice-token-m"]
                        assert MockGraphClient.seen_token_g == [
                            "token-g-for:alice-token-m"
                        ]
        finally:
            server.should_exit = True
            async with asyncio.timeout(5):
                await server_task

    asyncio.run(run_smoke_test())
