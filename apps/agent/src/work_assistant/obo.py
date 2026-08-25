"""Microsoft Entra on-behalf-of acquisition for the MCP audience token."""

from __future__ import annotations

import json
import logging
from typing import Protocol

import anyio
import jwt
import msal
from jwt.exceptions import PyJWTError

from work_assistant.config import Settings

logger = logging.getLogger(__name__)


class OboTokenError(RuntimeError):
    """A sanitized OBO failure safe to classify at the API boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MCPTokenAcquirer(Protocol):
    async def acquire_mcp_token(self, token_a: str) -> str: ...


class OboTokenService:
    """Exchange a validated Work Assistant Token A for MCP Token M."""

    def __init__(self, settings: Settings) -> None:
        self._client_id = str(settings.entra_work_assistant_api_client_id)
        self._authority = (
            f"https://login.microsoftonline.com/{settings.entra_tenant_id}"
        )
        self._client_secret = (
            settings.entra_work_assistant_api_client_secret.get_secret_value()
        )
        self._scope = settings.mcp_scope
        self._log_token_claims = settings.entra_log_mcp_token_claims

    async def acquire_mcp_token(self, token_a: str) -> str:
        if not token_a.strip():
            raise OboTokenError("invalid_user_assertion")

        try:
            result = await anyio.to_thread.run_sync(
                self._acquire_mcp_token_sync,
                token_a,
            )
        except Exception:
            logger.warning("MCP OBO token exchange failed")
            raise OboTokenError("obo_service_unavailable") from None

        access_token = result.get("access_token") if isinstance(result, dict) else None
        if isinstance(access_token, str) and access_token.strip():
            self._log_unverified_token_claims(access_token)
            return access_token

        error_code = result.get("error") if isinstance(result, dict) else None
        if error_code in {"interaction_required", "consent_required", "invalid_grant"}:
            safe_code = "obo_authorization_required"
        else:
            safe_code = "obo_token_exchange_failed"
        logger.warning("MCP OBO token exchange failed code=%s", safe_code)
        raise OboTokenError(safe_code)

    def _log_unverified_token_claims(self, token: str) -> None:
        """Decode Token M for explicitly enabled local troubleshooting only."""
        if not self._log_token_claims:
            return

        try:
            header = jwt.get_unverified_header(token)
            claims = jwt.decode(token, options={"verify_signature": False})
        except PyJWTError:
            logger.warning(
                "MCP Token M troubleshooting decode failed; raw token omitted"
            )
            return

        logger.info(
            "MCP Token M unverified diagnostic header=%s claims=%s",
            json.dumps(header, separators=(",", ":"), sort_keys=True),
            json.dumps(claims, separators=(",", ":"), sort_keys=True),
        )

    def _acquire_mcp_token_sync(self, token_a: str) -> dict[str, object]:
        """Use the Agent API's confidential identity for the OBO exchange."""
        application = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            authority=self._authority,
            client_credential=self._client_secret,
        )
        return application.acquire_token_on_behalf_of(
            user_assertion=token_a,
            scopes=[self._scope],
        )
