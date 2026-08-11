from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from work_assistant.agent import AgentServiceError
from work_assistant.app import ChatService, create_app
from work_assistant.auth import CurrentUser
from work_assistant.mcp import MCPConnectionError
from work_assistant.models import ChatResponse, Source


class FakeAgent:
    async def chat(self, thread_id: str, message: str) -> ChatResponse:
        return ChatResponse(
            thread_id=thread_id,
            answer=f"answer: {message}",
            sources=[Source(name="KB.docx", url="https://tenant.example/kb")],
        )


@asynccontextmanager
async def fake_service() -> AsyncIterator[ChatService]:
    yield FakeAgent()


async def fake_current_user() -> CurrentUser:
    return CurrentUser(
        oid="alice-oid",
        tid="tenant-id",
        username="alice@example.com",
    )


def test_health() -> None:
    with TestClient(create_app(fake_service)) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_chat_requires_bearer_token() -> None:
    with TestClient(create_app(fake_service)) as client:
        response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_returns_authenticated_identity() -> None:
    application = create_app(
        fake_service,
        current_user_dependency=fake_current_user,
    )
    with TestClient(application) as client:
        response = client.get("/me")

    assert response.status_code == 200
    assert response.json() == {
        "oid": "alice-oid",
        "tid": "tenant-id",
        "username": "alice@example.com",
    }


def test_chat_uses_mocked_agent_and_generates_thread_id() -> None:
    application = create_app(
        fake_service,
        current_user_dependency=fake_current_user,
    )
    with TestClient(application) as client:
        response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"]
    assert body["answer"] == "answer: hello"
    assert body["sources"] == [
        {"name": "KB.docx", "url": "https://tenant.example/kb"}
    ]


def test_chat_request_validation() -> None:
    application = create_app(
        fake_service,
        current_user_dependency=fake_current_user,
    )
    with TestClient(application) as client:
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

    @asynccontextmanager
    async def failing_service() -> AsyncIterator[ChatService]:
        yield FailingAgent()

    application = create_app(
        failing_service,
        current_user_dependency=fake_current_user,
    )
    with TestClient(application) as client:
        response = client.post("/chat", json={"message": "find policy"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "m365_rate_limited"


def test_mcp_startup_error_keeps_health_but_disables_chat() -> None:
    @asynccontextmanager
    async def broken_mcp() -> AsyncIterator[ChatService]:
        raise MCPConnectionError("raw subprocess detail")
        yield FakeAgent()  # pragma: no cover

    application = create_app(
        broken_mcp,
        current_user_dependency=fake_current_user,
    )
    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
        response = client.post("/chat", json={"message": "find policy"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "mcp_unavailable",
            "message": "The Microsoft 365 knowledge service could not be reached.",
        }
    }
