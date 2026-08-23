import logging

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from work_assistant.agent import AgentServiceError
from work_assistant.app import ChatService, create_app
from work_assistant.auth import (
    AuthenticatedRequest,
    CurrentUser,
    get_authenticated_request,
)


class FakeAgent:
    async def dispatch_chat(
        self,
        request: Request,
        authenticated: AuthenticatedRequest,
    ) -> Response:
        body = await request.json()
        return JSONResponse(
            {
                "threadId": body["threadId"],
                "username": authenticated.user.username,
            }
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


def _ag_ui_input() -> dict:
    return {
        "threadId": "thread-1",
        "runId": "run-1",
        "state": {},
        "messages": [{"id": "message-1", "role": "user", "content": "hello"}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def test_health() -> None:
    with TestClient(create_app(FakeAgent())) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_chat_requires_bearer_token() -> None:
    with TestClient(create_app(FakeAgent())) as client:
        response = client.post("/chat", json=_ag_ui_input())

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


def test_chat_passes_ag_ui_request_and_authenticated_user_to_service() -> None:
    with TestClient(_create_test_app()) as client:
        response = client.post("/chat", json=_ag_ui_input())

    assert response.status_code == 200
    assert response.json() == {
        "threadId": "thread-1",
        "username": "alice@example.com",
    }


def test_custom_action_endpoint_has_been_removed() -> None:
    with TestClient(_create_test_app()) as client:
        response = client.post(
            "/chat/actions/action-1/decision",
            json={"thread_id": "thread-1", "decision": "approve"},
        )

    assert response.status_code == 404


def test_agent_error_becomes_sanitized_api_error() -> None:
    class FailingAgent:
        async def dispatch_chat(
            self,
            request: Request,
            authenticated: AuthenticatedRequest,
        ) -> Response:
            raise AgentServiceError(
                503,
                "m365_rate_limited",
                "Microsoft 365 is throttling requests. Wait briefly and retry.",
            )

    with TestClient(_create_test_app(FailingAgent())) as client:
        response = client.post("/chat", json=_ag_ui_input())

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "m365_rate_limited"


def test_unexpected_service_error_is_logged_with_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingAgent:
        async def dispatch_chat(
            self,
            request: Request,
            authenticated: AuthenticatedRequest,
        ) -> Response:
            raise RuntimeError("unexpected service failure")

    with caplog.at_level(logging.ERROR, logger="work_assistant.app"):
        with TestClient(_create_test_app(FailingAgent())) as client:
            response = client.post("/chat", json=_ag_ui_input())

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "internal_error"
    record = next(
        record
        for record in caplog.records
        if record.name == "work_assistant.app"
        and record.getMessage() == "Chat request failed type=RuntimeError"
    )
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError
    assert "unexpected service failure" in caplog.text
