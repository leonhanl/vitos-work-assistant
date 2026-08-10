"""Minimal official MCP client for the local Streamable HTTP server."""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def run(endpoint: str, query: str) -> None:
    async with streamable_http_client(endpoint) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("Tools:", ", ".join(tool.name for tool in tools.tools))

            result = await session.call_tool(
                "search_sharepoint", {"query": query, "top": 5}
            )
            print(
                json.dumps(
                    result.model_dump(mode="json"), ensure_ascii=False, indent=2
                )
            )


def _contains_connect_error(error: BaseException) -> bool:
    if isinstance(error, httpx.ConnectError):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_contains_connect_error(child) for child in error.exceptions)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--query", default="VPN")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.endpoint, args.query))
    except (httpx.ConnectError, BaseExceptionGroup) as exc:
        if not _contains_connect_error(exc):
            raise
        parser.exit(
            1,
            f"Could not connect to the MCP server at {args.endpoint}.\n"
            "Start it in another terminal and keep it running:\n\n"
            "  set -a\n"
            "  source .env\n"
            "  set +a\n"
            "  python -m m365_mcp.server\n\n",
        )


if __name__ == "__main__":
    main()
