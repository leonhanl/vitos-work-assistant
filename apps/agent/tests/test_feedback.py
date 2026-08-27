import asyncio
import logging

import httpx
import pytest

from work_assistant.feedback import PortkeyFeedbackError, PortkeyFeedbackService


def test_portkey_feedback_service_sends_expected_request(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "status": "success",
                "message": "Feedback saved",
                "feedback_ids": ["feedback-1"],
            }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    service = PortkeyFeedbackService(
        "https://api.portkey.example/v1/",
        "secret-key",
    )

    with caplog.at_level(logging.INFO, logger="work_assistant.feedback"):
        asyncio.run(
            service.submit(
                "11111111-1111-4111-8111-111111111111",
                1,
                "alice@example.com",
            )
        )

    assert captured == {
        "timeout": 5.0,
        "url": "https://api.portkey.example/v1/feedback",
        "headers": {"x-portkey-api-key": "secret-key"},
        "json": {
            "trace_id": "11111111-1111-4111-8111-111111111111",
            "value": 1,
            "metadata": {"_user": "alice@example.com"},
        },
    }
    assert (
        "Portkey feedback recorded "
        "trace_id=11111111-1111-4111-8111-111111111111 "
        "feedback_ids=feedback-1"
    ) in caplog.text


def test_portkey_feedback_service_maps_network_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url, *, headers, json):
            raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx, "AsyncClient", FailingAsyncClient)
    service = PortkeyFeedbackService(
        "https://api.portkey.example/v1",
        "secret-key",
    )

    with pytest.raises(PortkeyFeedbackError):
        asyncio.run(
            service.submit(
                "11111111-1111-4111-8111-111111111111",
                -1,
                "alice@example.com",
            )
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "failure",
            "message": "Trace was not found",
            "feedback_ids": [],
        },
        {
            "status": "success",
            "message": "Feedback saved",
            "feedback_ids": [],
        },
    ],
)
def test_portkey_feedback_service_rejects_unsaved_200_response(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return payload

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url, *, headers, json):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    service = PortkeyFeedbackService(
        "https://api.portkey.example/v1",
        "secret-key",
    )

    with pytest.raises(PortkeyFeedbackError):
        asyncio.run(
            service.submit(
                "11111111-1111-4111-8111-111111111111",
                1,
                "alice@example.com",
            )
        )
