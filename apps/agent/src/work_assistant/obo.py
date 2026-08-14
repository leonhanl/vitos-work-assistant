"""Request-scoped Microsoft Entra on-behalf-of token acquisition."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol

import anyio
import msal

from work_assistant.auth import CurrentUser
from work_assistant.config import Settings

logger = logging.getLogger(__name__)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class OboTokenError(RuntimeError):
    """A sanitized OBO failure safe to classify at the API boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GraphTokenAcquirer(Protocol):
    async def acquire_graph_token(self, token_a: str) -> str: ...


class OboTokenService:
    """Exchange a validated Work Assistant Token A for a Graph Token B."""

    def __init__(self, settings: Settings) -> None:
        self._client_id = str(settings.entra_work_assistant_api_client_id)
        self._authority = (
            f"https://login.microsoftonline.com/{settings.entra_tenant_id}"
        )
        self._client_secret = (
            settings.entra_work_assistant_api_client_secret.get_secret_value()
        )

    async def acquire_graph_token(self, token_a: str) -> str:
        if not token_a.strip():
            raise OboTokenError("invalid_user_assertion")

        try:
            result = await anyio.to_thread.run_sync(
                self._acquire_graph_token_sync,
                token_a,
            )
        except Exception:
            logger.warning("Microsoft Graph OBO token exchange failed")
            raise OboTokenError("obo_service_unavailable") from None

        access_token = result.get("access_token") if isinstance(result, dict) else None
        if isinstance(access_token, str) and access_token.strip():
            return access_token

        error_code = result.get("error") if isinstance(result, dict) else None
        if error_code in {"interaction_required", "consent_required", "invalid_grant"}:
            safe_code = "obo_authorization_required"
        else:
            safe_code = "obo_token_exchange_failed"
        logger.warning("Microsoft Graph OBO token exchange failed code=%s", safe_code)
        raise OboTokenError(safe_code)

    def _acquire_graph_token_sync(self, token_a: str) -> dict[str, object]:
        """Create the confidential client only when an M365 tool first needs it."""
        application = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            authority=self._authority,
            client_credential=self._client_secret,
        )
        return application.acquire_token_on_behalf_of(
            user_assertion=token_a,
            scopes=[GRAPH_SCOPE],
        )


@dataclass(repr=False)
class RequestAuthContext:
    """Own Token A and lazily acquire at most one Token B for a chat request."""

    user: CurrentUser
    token_a: str = field(repr=False)
    token_acquirer: GraphTokenAcquirer = field(repr=False)
    _token_b: str | None = field(default=None, init=False, repr=False)
    _token_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def get_graph_token(self) -> str:
        if self._token_b is not None:
            return self._token_b

        async with self._token_lock:
            if self._token_b is None:
                logger.info(
                    "Microsoft Graph OBO token exchange started oid=%s tid=%s",
                    self.user.oid,
                    self.user.tid,
                )
                self._token_b = await self.token_acquirer.acquire_graph_token(
                    self.token_a
                )
                logger.info(
                    "Microsoft Graph OBO token exchange succeeded oid=%s tid=%s",
                    self.user.oid,
                    self.user.tid,
                )
        assert self._token_b is not None
        return self._token_b
