"""Microsoft Entra on-behalf-of exchange from Token M to a Graph token."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import msal

from m365_mcp.config import Settings

logger = logging.getLogger(__name__)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class OboTokenError(RuntimeError):
    """A sanitized OBO failure that never contains provider token details."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GraphTokenAcquirer(Protocol):
    async def acquire_graph_token(self, token_m: str) -> str: ...


class OboTokenService:
    """Exchange a validated MCP Token M for a delegated Graph Token G."""

    def __init__(self, settings: Settings) -> None:
        self._client_id = str(settings.entra_mcp_client_id)
        self._authority = (
            f"https://login.microsoftonline.com/{settings.entra_tenant_id}"
        )
        self._client_secret = settings.entra_mcp_client_secret.get_secret_value()

    async def acquire_graph_token(self, token_m: str) -> str:
        if not token_m.strip():
            raise OboTokenError("invalid_user_assertion")

        try:
            result = await asyncio.to_thread(
                self._acquire_graph_token_sync,
                token_m,
            )
        except Exception:
            logger.warning("Microsoft Graph OBO token exchange failed")
            raise OboTokenError("obo_service_unavailable") from None

        access_token = result.get("access_token") if isinstance(result, dict) else None
        if isinstance(access_token, str) and access_token.strip():
            logger.info("Microsoft Graph OBO token exchange succeeded")
            return access_token

        error_code = result.get("error") if isinstance(result, dict) else None
        if error_code in {"interaction_required", "consent_required", "invalid_grant"}:
            safe_code = "obo_authorization_required"
        else:
            safe_code = "obo_token_exchange_failed"
        logger.warning("Microsoft Graph OBO token exchange failed code=%s", safe_code)
        raise OboTokenError(safe_code)

    def _acquire_graph_token_sync(self, token_m: str) -> dict[str, object]:
        application = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            authority=self._authority,
            client_credential=self._client_secret,
        )
        return application.acquire_token_on_behalf_of(
            user_assertion=token_m,
            scopes=[GRAPH_SCOPE],
        )
