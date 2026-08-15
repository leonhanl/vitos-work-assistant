"""Small Microsoft Graph client for the capabilities exposed by this server."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
logger = logging.getLogger(__name__)


class GraphAPIError(RuntimeError):
    """A sanitized Microsoft Graph error safe to return through MCP."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def normalize_search_results(payload: object) -> list[dict[str, Any]]:
    """Normalize Microsoft Search's nested response into compact drive-item hits."""
    if not isinstance(payload, dict):
        return []

    normalized: list[dict[str, Any]] = []
    values = payload.get("value")
    if not isinstance(values, list):
        return normalized

    for value in values:
        if not isinstance(value, dict):
            continue
        containers = value.get("hitsContainers")
        if not isinstance(containers, list):
            continue
        for container in containers:
            if not isinstance(container, dict):
                continue
            hits = container.get("hits")
            if not isinstance(hits, list):
                continue
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                resource = hit.get("resource")
                if not isinstance(resource, dict):
                    resource = {}
                parent = resource.get("parentReference")
                if not isinstance(parent, dict):
                    parent = {}
                normalized.append(
                    {
                        "rank": hit.get("rank"),
                        "name": resource.get("name"),
                        "summary": hit.get("summary"),
                        "web_url": resource.get("webUrl"),
                        "drive_id": parent.get("driveId"),
                        "item_id": resource.get("id"),
                    }
                )
    return normalized


class GraphClient:
    """Minimal async client for Microsoft Search and drive-item reads."""

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
        )

    async def __aenter__(self) -> GraphClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def search_files(self, query: str, top: int = 5) -> list[dict[str, Any]]:
        response = await self._request(
            "POST",
            "/search/query",
            json={
                "requests": [
                    {
                        "entityTypes": ["driveItem"],
                        "query": {"queryString": query},
                        "from": 0,
                        "size": top,
                    }
                ]
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphAPIError(
                "Microsoft Graph Search returned an invalid JSON response."
            ) from exc
        return normalize_search_results(payload)

    async def get_drive_item(self, drive_id: str, item_id: str) -> dict[str, Any]:
        path = self._drive_item_path(drive_id, item_id)
        response = await self._request(
            "GET",
            path,
            params={"$select": "id,name,webUrl,file"},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphAPIError(
                "Microsoft Graph returned invalid document metadata."
            ) from exc
        if not isinstance(payload, dict):
            raise GraphAPIError("Microsoft Graph returned invalid document metadata.")
        return payload

    async def download_drive_item(self, drive_id: str, item_id: str) -> bytes:
        path = self._drive_item_path(drive_id, item_id) + "/content"
        response = await self._request("GET", path)
        return response.content

    @staticmethod
    def _drive_item_path(drive_id: str, item_id: str) -> str:
        safe_drive_id = quote(drive_id, safe="")
        safe_item_id = quote(item_id, safe="")
        return f"/drives/{safe_drive_id}/items/{safe_item_id}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = self._token_provider()
        try:
            response = await self._http.request(
                method,
                GRAPH_BASE_URL + path,
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
        except httpx.TimeoutException:
            logger.warning("Microsoft Graph timeout method=%s path=%s", method, path)
            raise GraphAPIError(
                "Microsoft Graph request timed out. Wait briefly and try again."
            ) from None
        except httpx.HTTPError:
            logger.warning(
                "Microsoft Graph network error method=%s path=%s", method, path
            )
            raise GraphAPIError(
                "Could not connect to Microsoft Graph. Check the network and try again."
            ) from None

        if response.status_code >= 400:
            logger.warning(
                "Microsoft Graph response status=%d method=%s path=%s",
                response.status_code,
                method,
                path,
            )
            raise self._map_error(response)
        return response

    @staticmethod
    def _map_error(response: httpx.Response) -> GraphAPIError:
        status = response.status_code
        if status == 401:
            message = "Microsoft Graph rejected the delegated access token (401)."
        elif status == 403:
            message = (
                "Microsoft Graph denied access (403). Check delegated Files.Read.All "
                "consent and the signed-in user's SharePoint permissions."
            )
        elif status == 404:
            message = "The requested Microsoft 365 document was not found (404)."
        elif status == 429:
            message = (
                "Microsoft Graph is throttling requests (429). Wait briefly and try again."
            )
        else:
            message = f"Microsoft Graph request failed with HTTP status {status}."
        return GraphAPIError(message, status_code=status)
