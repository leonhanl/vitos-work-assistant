import asyncio

import httpx
import pytest

from m365_mcp.graph import GraphAPIError, GraphClient, normalize_search_results


def test_normalize_search_results() -> None:
    payload = {
        "value": [
            {
                "hitsContainers": [
                    {
                        "hits": [
                            {
                                "rank": 1,
                                "summary": "Connect with the company VPN client.",
                                "resource": {
                                    "id": "item-123",
                                    "name": "VPN Guide.docx",
                                    "webUrl": "https://contoso.sharepoint.com/vpn",
                                    "parentReference": {"driveId": "drive-456"},
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    assert normalize_search_results(payload) == [
        {
            "rank": 1,
            "name": "VPN Guide.docx",
            "summary": "Connect with the company VPN client.",
            "web_url": "https://contoso.sharepoint.com/vpn",
            "drive_id": "drive-456",
            "item_id": "item-123",
        }
    ]


def test_normalize_search_hit_without_drive_id() -> None:
    payload = {
        "value": [
            {
                "hitsContainers": [
                    {
                        "hits": [
                            {
                                "rank": 2,
                                "resource": {"id": "item-only", "name": "Notes.md"},
                            }
                        ]
                    }
                ]
            }
        ]
    }

    result = normalize_search_results(payload)

    assert len(result) == 1
    assert result[0]["item_id"] == "item-only"
    assert result[0]["drive_id"] is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "delegated access token"),
        (403, "denied access"),
        (404, "not found"),
        (429, "throttling"),
        (500, "HTTP status 500"),
    ],
)
def test_graph_api_error_mapping(status: int, expected: str) -> None:
    async def run_request() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, request=request, json={"error": "not exposed"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = GraphClient(lambda: "fake-access-token", http_client=http_client)
            with pytest.raises(GraphAPIError, match=expected) as error:
                await client.get_drive_item("drive", "item")
            assert error.value.status_code == status
            assert "fake-access-token" not in str(error.value)

    asyncio.run(run_request())


def test_download_redirect_does_not_forward_graph_token_to_another_host() -> None:
    async def run_request() -> None:
        seen_authorization: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "graph.microsoft.com":
                return httpx.Response(
                    302,
                    request=request,
                    headers={"Location": "https://download.example/document"},
                )
            seen_authorization["value"] = request.headers.get("Authorization")
            return httpx.Response(200, request=request, content=b"document")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as http_client:
            client = GraphClient(lambda: "fake-access-token", http_client=http_client)
            assert await client.download_drive_item("drive", "item") == b"document"

        assert seen_authorization["value"] is None

    asyncio.run(run_request())
