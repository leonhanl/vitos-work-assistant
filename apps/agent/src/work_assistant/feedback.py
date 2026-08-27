"""Minimal Portkey feedback client."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class PortkeyFeedbackError(RuntimeError):
    """Portkey did not accept a feedback event."""


class PortkeyFeedbackService:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._url = f"{base_url.rstrip('/')}/feedback"
        self._api_key = api_key

    async def submit(self, trace_id: str, value: int, user: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    self._url,
                    headers={"x-portkey-api-key": self._api_key},
                    json={
                        "trace_id": trace_id,
                        "value": value,
                        "metadata": {"_user": user},
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PortkeyFeedbackError("Portkey feedback request failed") from exc

        status = payload.get("status") if isinstance(payload, dict) else None
        feedback_ids = (
            payload.get("feedback_ids") if isinstance(payload, dict) else None
        )
        if (
            status != "success"
            or not isinstance(feedback_ids, list)
            or not feedback_ids
            or not all(
                isinstance(feedback_id, str) for feedback_id in feedback_ids
            )
        ):
            message = payload.get("message") if isinstance(payload, dict) else None
            if not isinstance(message, str):
                message = "invalid response"
            raise PortkeyFeedbackError(
                f"Portkey feedback was not recorded: {message[:200]}"
            )

        logger.info(
            "Portkey feedback recorded trace_id=%s feedback_ids=%s",
            trace_id,
            ",".join(feedback_ids),
        )
