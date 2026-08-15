"""Authenticated Streamable HTTP MCP server for Microsoft 365 tools."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.auth.middleware.auth_context import (
    get_access_token as get_mcp_access_token,
)
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from m365_mcp.auth import EntraTokenVerifier
from m365_mcp.config import Settings
from m365_mcp.document_parser import (
    DocumentParseError,
    extract_text,
    validate_supported_type,
)
from m365_mcp.graph import GraphClient
from m365_mcp.obo import GraphTokenAcquirer, OboTokenService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class AuthenticationError(RuntimeError):
    """A safe error raised when request authentication context is unavailable."""


def create_mcp_server(
    settings: Settings,
    *,
    token_verifier: TokenVerifier | None = None,
    token_acquirer: GraphTokenAcquirer | None = None,
) -> FastMCP:
    """Create one configured MCP server and its request-scoped tool closures."""
    verifier = token_verifier or EntraTokenVerifier(
        tenant_id=str(settings.entra_tenant_id),
        audience=str(settings.entra_mcp_client_id),
        required_scope=settings.entra_required_scope,
        authorization_scope=settings.authorization_scope,
    )
    graph_token_acquirer = token_acquirer or OboTokenService(settings)

    mcp = FastMCP(
        "vitos-m365-mcp",
        instructions=(
            "Search and read Microsoft 365/SharePoint documents accessible to the "
            "currently signed-in user. Search first, then read a selected result."
        ),
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path=settings.mcp_path,
        stateless_http=True,
        json_response=True,
        log_level="INFO",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=settings.issuer_url,
            resource_server_url=settings.mcp_resource_url,
            required_scopes=[settings.authorization_scope],
        ),
    )

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> JSONResponse:
        """Return public liveness without contacting Entra or Graph."""
        return JSONResponse({"status": "ok"})

    async def graph_access_token() -> str:
        auth_info = get_mcp_access_token()
        if auth_info is None:
            raise AuthenticationError("The MCP authentication context is missing.")
        return await graph_token_acquirer.acquire_graph_token(auth_info.token)

    @mcp.tool()
    async def search_sharepoint(query: str, top: int = 5) -> list[dict[str, Any]]:
        """Search enterprise documents accessible to the current Microsoft 365 user.

        Use this for IT knowledge-base articles, company policies, operating manuals,
        internal documents, and product documents in SharePoint. Prefer a concise
        lexical query over a long natural-language question. An empty list means no
        results were found.
        """
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty.")
        if not 1 <= top <= 50:
            raise ValueError("top must be between 1 and 50.")

        logger.info("Graph search started top=%d", top)
        try:
            token_g = await graph_access_token()
            async with GraphClient(lambda: token_g) as client:
                results = await client.search_files(query, top)
        except Exception:
            logger.exception("MCP tool failure tool=search_sharepoint")
            raise
        logger.info("Graph search completed result_count=%d", len(results))
        return results

    @mcp.tool()
    async def read_document(drive_id: str, item_id: str) -> dict[str, str | None]:
        """Read the full text of a document returned by search_sharepoint.

        Usually call search_sharepoint first, then pass a selected result's drive_id
        and item_id to this tool. DOCX, UTF-8 TXT, and Markdown are supported; PDF is
        not.
        """
        drive_id = drive_id.strip()
        item_id = item_id.strip()
        if not drive_id or not item_id:
            raise ValueError("drive_id and item_id must not be empty.")

        logger.info("Document read started")
        try:
            token_g = await graph_access_token()
            async with GraphClient(lambda: token_g) as client:
                metadata = await client.get_drive_item(drive_id, item_id)
                name = metadata.get("name")
                if not isinstance(name, str) or not name:
                    raise DocumentParseError(
                        "Microsoft Graph returned document metadata without a valid "
                        "name."
                    )

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

    return mcp


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def create_http_app(
    settings: Settings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    token_acquirer: GraphTokenAcquirer | None = None,
) -> ASGIApp:
    """Create the authenticated Streamable HTTP application."""
    configured_settings = settings or Settings()
    return create_mcp_server(
        configured_settings,
        token_verifier=token_verifier,
        token_acquirer=token_acquirer,
    ).streamable_http_app()


def main() -> None:
    settings = Settings()
    logger.info(
        "MCP server starting transport=streamable-http host=%s port=%d path=%s "
        "stateless=true auth=entra-obo",
        settings.mcp_host,
        settings.mcp_port,
        settings.mcp_path,
    )
    if settings.mcp_host not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning(
            "MCP server is listening beyond localhost; terminate TLS at a trusted "
            "ingress and ensure MCP_RESOURCE_URL uses the public HTTPS endpoint"
        )
    try:
        import uvicorn

        uvicorn.run(
            create_http_app(settings),
            host=settings.mcp_host,
            port=settings.mcp_port,
            log_level="info",
        )
    except KeyboardInterrupt:
        logger.info("MCP server shutdown requested")
    finally:
        logger.info("MCP server shutdown completed")


if __name__ == "__main__":
    main()
