"""Request-scoped Microsoft Graph authentication for MCP tool calls."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

_graph_token: ContextVar[str | None] = ContextVar("graph_access_token", default=None)


class AuthenticationError(RuntimeError):
    """A safe authentication error containing no token data."""


class GraphTokenMiddleware:
    """Bind an incoming Bearer token to one stateless MCP HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        token = _bearer_token(scope) if scope["type"] == "http" else None
        marker = _graph_token.set(token)
        try:
            await self._app(scope, receive, send)
        finally:
            _graph_token.reset(marker)


def get_access_token() -> str:
    """Return Token B for the current tool call."""
    token = _graph_token.get()
    if token is None:
        raise AuthenticationError(
            "The Microsoft 365 tool call is missing a Graph access token."
        )
    return token


def _bearer_token(scope: dict[str, Any]) -> str | None:
    value = Headers(scope=scope).get("authorization")
    if not value:
        return None
    scheme, separator, credentials = value.partition(" ")
    if separator and scheme.lower() == "bearer" and credentials.strip():
        return credentials.strip()
    return None
