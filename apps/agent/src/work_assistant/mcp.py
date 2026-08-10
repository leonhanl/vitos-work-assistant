"""Persistent Streamable HTTP connection to the Microsoft 365 MCP service."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from work_assistant.config import Settings

logger = logging.getLogger(__name__)

SERVER_NAME = "m365-mcp-http"
EXPECTED_TOOLS = {"search_sharepoint", "read_document"}


class MCPConnectionError(RuntimeError):
    """Raised when the narrow Microsoft 365 MCP contract is unavailable."""


class M365MCPClient:
    """Own one Streamable HTTP MCP session for the API lifespan."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stack = AsyncExitStack()
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
            client = MultiServerMCPClient({SERVER_NAME: connection})
            session = await self._stack.enter_async_context(client.session(SERVER_NAME))
            tools = await load_mcp_tools(session, handle_tool_errors=False)
            names = {tool.name for tool in tools}
            missing = EXPECTED_TOOLS - names
            if missing:
                missing_names = ", ".join(sorted(missing))
                raise MCPConnectionError(
                    f"m365-mcp-http is missing required tool(s): {missing_names}"
                )
            self._connected = True
            logger.info("Connected to m365-mcp-http over Streamable HTTP")
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
        await self._stack.aclose()
        self._stack = AsyncExitStack()
        self._connected = False
