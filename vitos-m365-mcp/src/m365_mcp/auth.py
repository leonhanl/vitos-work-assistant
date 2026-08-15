"""Microsoft Entra access-token verification for the MCP resource server."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    ImmatureSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError as PyJWTInvalidTokenError,
    MissingRequiredClaimError,
    PyJWKClientConnectionError,
    PyJWKClientError,
)
from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)

ALGORITHMS = ["RS256"]
CLOCK_SKEW_SECONDS = 30
METADATA_TIMEOUT_SECONDS = 10.0


class TokenValidationError(RuntimeError):
    """A rejected Token M with a safe reason suitable for logs."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AuthenticationServiceError(RuntimeError):
    """Entra metadata or signing keys could not be obtained safely."""


@dataclass(frozen=True)
class OIDCMetadata:
    issuer: str
    jwks_uri: str


@dataclass(frozen=True)
class ValidationContext:
    issuer: str
    signing_key: Any


class EntraTokenVerifier:
    """Validate single-tenant v2 delegated tokens issued for this MCP server."""

    def __init__(
        self,
        tenant_id: str,
        audience: str,
        required_scope: str,
        authorization_scope: str,
    ) -> None:
        self.tenant_id = tenant_id.lower()
        self.audience = audience.lower()
        self.required_scope = required_scope
        self.authorization_scope = authorization_scope
        self._metadata: OIDCMetadata | None = None
        self._jwks_client: PyJWKClient | None = None
        self._initialization_lock = asyncio.Lock()

    @property
    def metadata_url(self) -> str:
        return (
            f"https://login.microsoftonline.com/{self.tenant_id}"
            "/v2.0/.well-known/openid-configuration"
        )

    @property
    def expected_issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return MCP auth information only after full JWT verification."""
        try:
            claims = await self._validate(token)
        except TokenValidationError as exc:
            logger.warning("MCP authentication failed reason=%s", exc.reason)
            return None
        except AuthenticationServiceError:
            logger.error("MCP authentication unavailable while validating Token M")
            return None

        scope_claim = claims.get("scp")
        scopes = scope_claim.split() if isinstance(scope_claim, str) else []
        # Entra emits the short value in `scp`, while RFC 9728 metadata must
        # advertise the fully-qualified API scope clients need to request.
        if self.required_scope in scopes:
            scopes.append(self.authorization_scope)

        oid = str(claims["oid"])
        tid = str(claims["tid"]).lower()
        authorized_party = str(claims["azp"])
        logger.info(
            "Authenticated MCP request oid=%s tid=%s azp=%s",
            oid,
            tid,
            authorized_party,
        )
        return AccessToken(
            token=token,
            client_id=authorized_party,
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=oid,
            claims={
                "iss": claims["iss"],
                "tid": tid,
                "oid": oid,
                "azp": authorized_party,
            },
        )

    async def _validate(self, token: str) -> dict[str, Any]:
        if not token.strip():
            raise TokenValidationError("empty_token")

        context = await self._resolve_validation_context(token)
        try:
            claims = jwt.decode(
                token,
                context.signing_key,
                algorithms=ALGORITHMS,
                audience=self.audience,
                issuer=context.issuer,
                leeway=CLOCK_SKEW_SECONDS,
                options={
                    "require": [
                        "aud",
                        "azp",
                        "exp",
                        "iss",
                        "nbf",
                        "oid",
                        "tid",
                        "ver",
                    ],
                    "strict_aud": True,
                },
            )
        except ExpiredSignatureError:
            raise TokenValidationError("expired_token") from None
        except ImmatureSignatureError:
            raise TokenValidationError("token_not_yet_valid") from None
        except InvalidAudienceError:
            raise TokenValidationError("invalid_audience") from None
        except InvalidIssuerError:
            raise TokenValidationError("invalid_issuer") from None
        except InvalidSignatureError:
            raise TokenValidationError("invalid_signature") from None
        except MissingRequiredClaimError as exc:
            raise TokenValidationError(f"missing_claim_{exc.claim}") from None
        except PyJWTInvalidTokenError:
            raise TokenValidationError("invalid_token") from None

        if claims.get("ver") != "2.0":
            raise TokenValidationError("unsupported_token_version")

        tid = claims.get("tid")
        if not isinstance(tid, str) or tid.lower() != self.tenant_id:
            raise TokenValidationError("invalid_tenant")

        for claim_name in ("oid", "azp"):
            value = claims.get(claim_name)
            if not isinstance(value, str) or not value.strip():
                raise TokenValidationError(f"invalid_{claim_name}")

        return claims

    async def _resolve_validation_context(self, token: str) -> ValidationContext:
        metadata, jwks_client = await self._get_metadata_and_jwks_client()
        try:
            signing_key = await asyncio.to_thread(
                jwks_client.get_signing_key_from_jwt,
                token,
            )
        except PyJWKClientConnectionError:
            raise AuthenticationServiceError(
                "Microsoft Entra signing keys are unavailable."
            ) from None
        except (PyJWKClientError, DecodeError):
            raise TokenValidationError("invalid_signing_key") from None
        return ValidationContext(metadata.issuer, signing_key.key)

    async def _get_metadata_and_jwks_client(
        self,
    ) -> tuple[OIDCMetadata, PyJWKClient]:
        if self._metadata is not None and self._jwks_client is not None:
            return self._metadata, self._jwks_client

        async with self._initialization_lock:
            if self._metadata is None or self._jwks_client is None:
                metadata = await self._fetch_metadata()
                self._metadata = metadata
                self._jwks_client = PyJWKClient(
                    metadata.jwks_uri,
                    cache_keys=True,
                    cache_jwk_set=True,
                    lifespan=24 * 60 * 60,
                    timeout=METADATA_TIMEOUT_SECONDS,
                )
        assert self._metadata is not None
        assert self._jwks_client is not None
        return self._metadata, self._jwks_client

    async def _fetch_metadata(self) -> OIDCMetadata:
        try:
            async with httpx.AsyncClient(
                timeout=METADATA_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                response = await client.get(self.metadata_url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise AuthenticationServiceError(
                "Microsoft Entra OIDC metadata is unavailable."
            ) from None

        if not isinstance(payload, dict):
            raise AuthenticationServiceError("Invalid Microsoft Entra OIDC metadata.")
        issuer = payload.get("issuer")
        jwks_uri = payload.get("jwks_uri")
        if not isinstance(issuer, str) or not isinstance(jwks_uri, str):
            raise AuthenticationServiceError("Invalid Microsoft Entra OIDC metadata.")
        if issuer.rstrip("/").lower() != self.expected_issuer.lower():
            raise AuthenticationServiceError("Unexpected Microsoft Entra issuer metadata.")

        parsed_jwks_uri = urlparse(jwks_uri)
        if (
            parsed_jwks_uri.scheme != "https"
            or parsed_jwks_uri.hostname != "login.microsoftonline.com"
        ):
            raise AuthenticationServiceError("Unexpected Microsoft Entra JWKS endpoint.")
        return OIDCMetadata(issuer=issuer.rstrip("/"), jwks_uri=jwks_uri)
