"""stdio MCP server exposing narrow, read-only Microsoft 365 tools."""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from m365_mcp.auth import get_access_token
from m365_mcp.document_parser import (
    DocumentParseError,
    extract_text,
    validate_supported_type,
)
from m365_mcp.graph import GraphClient

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

mcp = FastMCP(
    "m365-mcp",
    instructions=(
        "Search and read Microsoft 365/SharePoint documents accessible to the "
        "currently signed-in user. Search first, then read a selected result."
    ),
    log_level="WARNING",
)


@mcp.tool()
async def search_sharepoint(query: str, top: int = 5) -> list[dict[str, Any]]:
    """搜索当前用户有权访问的 Microsoft 365 / SharePoint 企业文档。

    适用于 IT KB、公司政策、操作手册、内部文档和产品文档。query 应尽量是
    简洁的搜索关键词，而不是很长的自然语言问题。结果为空列表表示没有命中。
    """
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty.")
    if not 1 <= top <= 50:
        raise ValueError("top must be between 1 and 50.")

    async with GraphClient(get_access_token) as client:
        return await client.search_files(query, top)


@mcp.tool()
async def read_document(drive_id: str, item_id: str) -> dict[str, str | None]:
    """读取 search_sharepoint 返回的某一篇文档的完整正文。

    通常先调用 search_sharepoint 找到候选文档，再将结果中的 drive_id 和
    item_id 传入本工具。当前支持 DOCX、UTF-8 TXT 和 Markdown，不支持 PDF。
    """
    drive_id = drive_id.strip()
    item_id = item_id.strip()
    if not drive_id or not item_id:
        raise ValueError("drive_id and item_id must not be empty.")

    async with GraphClient(get_access_token) as client:
        metadata = await client.get_drive_item(drive_id, item_id)
        name = metadata.get("name")
        if not isinstance(name, str) or not name:
            raise DocumentParseError(
                "Microsoft Graph returned document metadata without a valid name."
            )

        # Reject unsupported formats before downloading their content.
        validate_supported_type(name)

        content = await client.download_drive_item(drive_id, item_id)
        return {
            "name": name,
            "web_url": _optional_string(metadata.get("webUrl")),
            "content": extract_text(name, content),
        }


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
