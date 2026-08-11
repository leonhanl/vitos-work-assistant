"""Microsoft Entra authentication for Work Assistant API access tokens."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any
from urllib.parse import urlparse

import anyio
import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
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
from pydantic import BaseModel, ValidationError

from work_assistant.config import Settings

logger = logging.getLogger(__name__)

ALGORITHMS = ["RS256"]
CLOCK_SKEW_SECONDS = 30
METADATA_TIMEOUT_SECONDS = 10.0

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="Microsoft Entra access token",
)


class CurrentUser(BaseModel):
    """Stable identity claims from a fully validated Token A."""

    oid: str
    tid: str
    username: str | None = None


class TokenValidationError(RuntimeError):
    """A rejected Token A with a safe reason for server-side logging."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class InsufficientScopeError(RuntimeError):
    """A valid Token A that lacks the delegated API scope."""


class AuthenticationServiceError(RuntimeError):
    """OIDC metadata or signing keys could not be obtained safely."""


@dataclass(frozen=True)
class OIDCMetadata:
    issuer: str
    jwks_uri: str


@dataclass(frozen=True)
class ValidationContext:
    issuer: str
    signing_key: Any


class EntraTokenValidator:
    """Validate single-tenant v2 access tokens issued for this API."""

    def __init__(self, tenant_id: str, audience: str, required_scope: str) -> None:
        self.tenant_id = tenant_id.lower()
        self.audience = audience.lower()
        self.required_scope = required_scope
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

    async def validate(self, token: str) -> CurrentUser:
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
                    "require": ["aud", "exp", "iss", "nbf", "oid", "tid", "ver"],
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

        oid = claims.get("oid")
        if not isinstance(oid, str) or not oid.strip():
            raise TokenValidationError("invalid_oid")

        scopes = claims.get("scp")
        granted_scopes = set(scopes.split()) if isinstance(scopes, str) else set()
        if self.required_scope not in granted_scopes:
            raise InsufficientScopeError(self.required_scope)

        username = claims.get("preferred_username")
        if not isinstance(username, str) or not username.strip():
            username = claims.get("upn")
        if not isinstance(username, str) or not username.strip():
            username = None

        return CurrentUser(
            oid=oid.strip(),
            tid=tid.lower(),
            username=username.strip() if username else None,
        )

    async def _resolve_validation_context(self, token: str) -> ValidationContext:
        metadata, jwks_client = await self._get_metadata_and_jwks_client()
        try:
            signing_key = await anyio.to_thread.run_sync(
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=4)
def _cached_validator(
    tenant_id: str,
    audience: str,
    required_scope: str,
) -> EntraTokenValidator:
    return EntraTokenValidator(tenant_id, audience, required_scope)


def get_token_validator() -> EntraTokenValidator:
    settings = get_settings()
    return _cached_validator(
        str(settings.entra_tenant_id),
        str(settings.entra_work_assistant_api_client_id),
        settings.entra_required_scope,
    )


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        logger.warning("Authentication failed reason=missing_bearer_token")
        raise _unauthorized()

    try:
        validator = get_token_validator()
        user = await validator.validate(credentials.credentials)
    except InsufficientScopeError as exc:
        logger.warning("Authentication forbidden reason=missing_scope scope=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Required delegated scope is missing.",
            headers={
                "WWW-Authenticate": (
                    'Bearer error="insufficient_scope", '
                    f'scope="{exc}"'
                )
            },
        ) from None
    except TokenValidationError as exc:
        logger.warning("Authentication failed reason=%s", exc.reason)
        raise _unauthorized() from None
    except (AuthenticationServiceError, ValidationError):
        logger.error("Authentication unavailable while validating Token A")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        ) from None

    logger.info(
        "Authenticated request oid=%s tid=%s username=%s",
        user.oid,
        user.tid,
        user.username,
    )
    return user


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid Work Assistant access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
