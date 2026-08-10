"""Streamable HTTP MCP server exposing read-only Microsoft 365 tools."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP

from m365_mcp.auth import get_access_token
from m365_mcp.document_parser import (
    DocumentParseError,
    extract_text,
    validate_supported_type,
)
from m365_mcp.graph import GraphClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# httpx includes complete redirect URLs in INFO records. SharePoint download
# redirects can contain short-lived authorization query parameters, so only
# expose this dependency's warnings and errors.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerConfig:
    """Network settings for the standalone Streamable HTTP service."""

    host: str = "127.0.0.1"
    port: int = 8001
    path: str = "/mcp"

    @classmethod
    def from_env(cls) -> ServerConfig:
        host = os.environ.get("MCP_HOST", cls.host).strip() or cls.host
        port_value = os.environ.get("MCP_PORT", str(cls.port)).strip()
        path = os.environ.get("MCP_PATH", cls.path).strip() or cls.path

        try:
            port = int(port_value)
        except ValueError as exc:
            raise ValueError("MCP_PORT must be an integer.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("MCP_PORT must be between 1 and 65535.")
        if not path.startswith("/"):
            raise ValueError("MCP_PATH must start with '/'.")

        return cls(host=host, port=port, path=path)


server_config = ServerConfig.from_env()

mcp = FastMCP(
    "m365-mcp-http",
    instructions=(
        "Search and read Microsoft 365/SharePoint documents accessible to the "
        "currently signed-in user. Search first, then read a selected result."
    ),
    host=server_config.host,
    port=server_config.port,
    streamable_http_path=server_config.path,
    stateless_http=True,
    json_response=True,
    log_level="INFO",
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

    logger.info("Graph search started top=%d", top)
    try:
        async with GraphClient(get_access_token) as client:
            results = await client.search_files(query, top)
    except Exception:
        logger.exception("MCP tool failure tool=search_sharepoint")
        raise
    logger.info("Graph search completed result_count=%d", len(results))
    return results


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

    logger.info("Document read started")
    try:
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
            result = {
                "name": name,
                "web_url": _optional_string(metadata.get("webUrl")),
                "content": extract_text(name, content),
            }
    except Exception:
        logger.exception("MCP tool failure tool=read_document")
        raise
    logger.info("Document read completed")
    return result


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def main() -> None:
    logger.info(
        "MCP server starting transport=streamable-http host=%s port=%d path=%s "
        "stateless=true",
        server_config.host,
        server_config.port,
        server_config.path,
    )
    if server_config.host not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning(
            "MCP server is listening beyond localhost without MCP HTTP "
            "authentication; use only in a trusted development environment"
        )
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logger.info("MCP server shutdown requested")
    finally:
        logger.info("MCP server shutdown completed")


if __name__ == "__main__":
    main()
