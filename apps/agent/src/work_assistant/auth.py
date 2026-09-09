"""Microsoft Entra authentication for Work Assistant API access tokens."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any

import anyio
import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import (
    DecodeError,
    InvalidTokenError as PyJWTInvalidTokenError,
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


@dataclass(frozen=True, repr=False)
class AuthenticatedRequest:
    """A validated user and the original Token A used for downstream OBO."""

    user: CurrentUser
    token_a: str = field(repr=False)


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
        except PyJWTInvalidTokenError as exc:
            # The PyJWT exception type names the rejection reason precisely enough
            # for server-side diagnosis, e.g. ExpiredSignatureError.
            raise TokenValidationError(type(exc).__name__) from None

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
        """Read the issuer and JWKS endpoint from Entra's OIDC discovery document."""
        try:
            async with httpx.AsyncClient(timeout=METADATA_TIMEOUT_SECONDS) as client:
                response = await client.get(self.metadata_url)
                response.raise_for_status()
                payload = response.json()
                return OIDCMetadata(
                    issuer=payload["issuer"],
                    jwks_uri=payload["jwks_uri"],
                )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise AuthenticationServiceError(
                "Microsoft Entra OIDC metadata is unavailable."
            ) from None


@lru_cache(maxsize=1)
def get_token_validator() -> EntraTokenValidator:
    """Create one validator and reuse its cached Entra signing keys."""
    settings = Settings()
    return EntraTokenValidator(
        str(settings.entra_tenant_id),
        str(settings.entra_work_assistant_api_client_id),
        settings.entra_required_scope,
    )


async def get_authenticated_request(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> AuthenticatedRequest:
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
    return AuthenticatedRequest(user=user, token_a=credentials.credentials)


async def get_current_user(
    authenticated: Annotated[
        AuthenticatedRequest,
        Depends(get_authenticated_request),
    ],
) -> CurrentUser:
    """Return only the public identity; Token A must never enter a response model."""
    return authenticated.user


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid Work Assistant access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
