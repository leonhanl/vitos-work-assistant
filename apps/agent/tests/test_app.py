import pytest
from fastapi.testclient import TestClient

import work_assistant.app as app_module
from work_assistant.agent import AgentServiceError
from work_assistant.app import ChatService, create_app
from work_assistant.auth import CurrentUser, get_current_user
from work_assistant.mcp import MCPConnectionError
from work_assistant.models import ChatResponse, Source


class FakeAgent:
    async def chat(self, thread_id: str, message: str) -> ChatResponse:
        return ChatResponse(
            thread_id=thread_id,
            answer=f"answer: {message}",
            sources=[Source(name="KB.docx", url="https://tenant.example/kb")],
        )


async def fake_current_user() -> CurrentUser:
    return CurrentUser(
        oid="alice-oid",
        tid="tenant-id",
        username="alice@example.com",
    )


def _create_test_app(chat_service: ChatService | None = None):
    application = create_app(chat_service or FakeAgent())
    application.dependency_overrides[get_current_user] = fake_current_user
    return application


def test_health() -> None:
    with TestClient(create_app(FakeAgent())) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_chat_requires_bearer_token() -> None:
    with TestClient(create_app(FakeAgent())) as client:
        response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_returns_authenticated_identity() -> None:
    with TestClient(_create_test_app()) as client:
        response = client.get("/me")

    assert response.status_code == 200
    assert response.json() == {
        "oid": "alice-oid",
        "tid": "tenant-id",
        "username": "alice@example.com",
    }


def test_chat_uses_mocked_agent_and_generates_thread_id() -> None:
    with TestClient(_create_test_app()) as client:
        response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"]
    assert body["answer"] == "answer: hello"
    assert body["sources"] == [
        {"name": "KB.docx", "url": "https://tenant.example/kb"}
    ]


def test_chat_request_validation() -> None:
    with TestClient(_create_test_app()) as client:
        response = client.post("/chat", json={"message": "   "})

    assert response.status_code == 422


def test_agent_error_becomes_sanitized_api_error() -> None:
    class FailingAgent:
        async def chat(self, thread_id: str, message: str) -> ChatResponse:
            raise AgentServiceError(
                503,
                "m365_rate_limited",
                "Microsoft 365 is throttling requests. Wait briefly and retry.",
            )

    with TestClient(_create_test_app(FailingAgent())) as client:
        response = client.post("/chat", json={"message": "find policy"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "m365_rate_limited"


def test_mcp_startup_failure_stops_the_app_and_closes_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = []

    class BrokenMCPClient:
        def __init__(self, settings: object) -> None:
            self.closed = False
            clients.append(self)

        async def connect(self):
            raise MCPConnectionError("MCP is unavailable")

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(app_module, "Settings", lambda: object())
    monkeypatch.setattr(app_module, "M365MCPClient", BrokenMCPClient)

    with pytest.raises(MCPConnectionError, match="MCP is unavailable"):
        with TestClient(create_app()):
            pass

    assert len(clients) == 1
    assert clients[0].closed is True
