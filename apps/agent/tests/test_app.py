from fastapi.testclient import TestClient

from work_assistant.agent import AgentServiceError
from work_assistant.app import ChatService, create_app
from work_assistant.auth import (
    AuthenticatedRequest,
    CurrentUser,
    get_authenticated_request,
)
from work_assistant.models import ChatResponse, Source


class FakeAgent:
    async def chat(
        self,
        thread_id: str,
        message: str,
        authenticated: AuthenticatedRequest,
    ) -> ChatResponse:
        return ChatResponse(
            thread_id=thread_id,
            answer=f"answer: {message}",
            sources=[Source(name="KB.docx", url="https://tenant.example/kb")],
        )


async def fake_authenticated_request() -> AuthenticatedRequest:
    return AuthenticatedRequest(
        user=CurrentUser(
            oid="alice-oid",
            tid="tenant-id",
            username="alice@example.com",
        ),
        token_a="token-a",
    )


def _create_test_app(chat_service: ChatService | None = None):
    application = create_app(chat_service or FakeAgent())
    application.dependency_overrides[
        get_authenticated_request
    ] = fake_authenticated_request
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
        async def chat(
            self,
            thread_id: str,
            message: str,
            authenticated: AuthenticatedRequest,
        ) -> ChatResponse:
            raise AgentServiceError(
                503,
                "m365_rate_limited",
                "Microsoft 365 is throttling requests. Wait briefly and retry.",
            )

    with TestClient(_create_test_app(FailingAgent())) as client:
        response = client.post("/chat", json={"message": "find policy"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "m365_rate_limited"
