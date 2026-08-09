"""Persistent stdio connection to the existing m365-mcp service."""

from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from work_assistant.config import Settings

logger = logging.getLogger(__name__)

SERVER_NAME = "m365-mcp"
EXPECTED_TOOLS = {"search_sharepoint", "read_document"}


class MCPConnectionError(RuntimeError):
    """Raised when the narrow Microsoft 365 MCP contract is unavailable."""


class M365MCPClient:
    """Own one MCP session (and stdio subprocess) for the API lifespan."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stack = AsyncExitStack()
        self._connected = False

    async def connect(self) -> list[BaseTool]:
        if self._connected:
            raise RuntimeError("M365 MCP client is already connected")

        workdir = self._settings.m365_mcp_working_directory
        self._validate_paths(workdir)
        connection: dict[str, Any] = {
            "transport": "stdio",
            "command": self._settings.m365_mcp_python,
            "args": ["-m", "m365_mcp.server"],
            "cwd": str(workdir),
        }

        # Keep LLM credentials out of the child process. The MCP SDK supplies its
        # normal safe process environment and overlays only these M365 settings.
        m365_env = {
            key: value
            for key in (
                "M365_TENANT_ID",
                "M365_CLIENT_ID",
                "M365_TOKEN_CACHE_PATH",
            )
            if (value := os.environ.get(key))
        }
        if m365_env:
            connection["env"] = m365_env

        try:
            client = MultiServerMCPClient({SERVER_NAME: connection})
            session = await self._stack.enter_async_context(client.session(SERVER_NAME))
            tools = await load_mcp_tools(session, handle_tool_errors=False)
            names = {tool.name for tool in tools}
            missing = EXPECTED_TOOLS - names
            if missing:
                missing_names = ", ".join(sorted(missing))
                raise MCPConnectionError(
                    f"m365-mcp is missing required tool(s): {missing_names}"
                )
            self._connected = True
            logger.info("Connected to m365-mcp over stdio")
            return [tool for tool in tools if tool.name in EXPECTED_TOOLS]
        except MCPConnectionError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise MCPConnectionError(
                "Could not start or connect to the m365-mcp stdio server."
            ) from exc

    async def close(self) -> None:
        await self._stack.aclose()
        self._stack = AsyncExitStack()
        self._connected = False

    def _validate_paths(self, workdir: Path) -> None:
        if not workdir.is_dir():
            raise MCPConnectionError("The services/m365-mcp directory was not found.")
        python = Path(self._settings.m365_mcp_python).expanduser()
        if python.is_absolute() and not python.is_file():
            raise MCPConnectionError("M365_MCP_PYTHON does not point to a file.")

