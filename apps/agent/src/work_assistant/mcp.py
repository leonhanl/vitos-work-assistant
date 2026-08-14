"""Request-authenticated tools for the Microsoft 365 MCP service."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import (
    MCPToolCallRequest,
    MCPToolCallResult,
)

from work_assistant.config import Settings
from work_assistant.obo import RequestAuthContext

logger = logging.getLogger(__name__)

SERVER_NAME = "m365-mcp-http"
EXPECTED_TOOLS = {"search_sharepoint", "read_document"}
_request_auth: ContextVar[RequestAuthContext | None] = ContextVar(
    "m365_request_auth",
    default=None,
)


class MCPConnectionError(RuntimeError):
    """Raised when the narrow Microsoft 365 MCP contract is unavailable."""


@contextmanager
def bind_request_auth(auth_context: RequestAuthContext) -> Iterator[None]:
    """Make one request's credentials visible only to its MCP tool calls."""
    marker = _request_auth.set(auth_context)
    try:
        yield
    finally:
        _request_auth.reset(marker)


async def _inject_graph_token(
    request: MCPToolCallRequest,
    handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
) -> MCPToolCallResult:
    """Lazily obtain Token B and add it to a single MCP tool session."""
    auth_context = _request_auth.get()
    if auth_context is None:
        raise RuntimeError("Microsoft 365 tool called outside an authenticated request")

    token_b = await auth_context.get_graph_token()
    headers = dict(request.headers or {})
    headers["Authorization"] = f"Bearer {token_b}"
    return await handler(request.override(headers=headers))


class M365MCPClient:
    """Load MCP schemas once and open a stateless session for each tool call."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connected = False

    async def connect(self) -> list[BaseTool]:
        if self._connected:
            raise RuntimeError("M365 MCP client is already connected")

        connection: dict[str, Any] = {
            "transport": "streamable_http",
            "url": str(self._settings.m365_mcp_url),
            # The M365 server is stateless; there is no server-side session to
            # terminate when the Agent process closes its client connection.
            "terminate_on_close": False,
        }

        try:
            client = MultiServerMCPClient(
                {SERVER_NAME: connection},
                tool_interceptors=[_inject_graph_token],
                handle_tool_errors=False,
            )
            tools = await client.get_tools(server_name=SERVER_NAME)
            names = {tool.name for tool in tools}
            missing = EXPECTED_TOOLS - names
            if missing:
                missing_names = ", ".join(sorted(missing))
                raise MCPConnectionError(
                    f"m365-mcp-http is missing required tool(s): {missing_names}"
                )
            self._connected = True
            logger.info("Loaded tools from m365-mcp-http over Streamable HTTP")
            return [tool for tool in tools if tool.name in EXPECTED_TOOLS]
        except MCPConnectionError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise MCPConnectionError(
                "Could not connect to the m365-mcp-http Streamable HTTP endpoint."
            ) from exc

    async def close(self) -> None:
        self._connected = False
