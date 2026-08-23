"""FastAPI entry point for Vito's Work Assistant."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.responses import Response

from work_assistant.agent import AgentService, AgentServiceError
from work_assistant.auth import (
    AuthenticatedRequest,
    CurrentUser,
    get_authenticated_request,
    get_current_user,
)
from work_assistant.config import Settings
from work_assistant.obo import OboTokenService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


class ChatService(Protocol):
    """The methods FastAPI needs from the Agent service."""

    async def dispatch_chat(
        self,
        request: Request,
        authenticated: AuthenticatedRequest,
    ) -> Response: ...


def create_app(chat_service: ChatService | None = None) -> FastAPI:
    """Create the API; tests may pass a small fake chat service."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # A supplied service keeps HTTP tests independent from MCP and the LLM.
        if chat_service is not None:
            application.state.chat_service = chat_service
            yield
            return

        settings = Settings()
        application.state.chat_service = AgentService(
            settings,
            OboTokenService(settings),
        )
        logger.info(
            "Skills artifact loaded version=%s directory=%s",
            settings.skills_version,
            settings.skills_directory,
        )
        yield

    application = FastAPI(
        title="Vito's Work Assistant API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/me", response_model=CurrentUser)
    async def me(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        return current_user

    @application.post("/chat")
    async def chat(
        request: Request,
        authenticated: AuthenticatedRequest = Depends(get_authenticated_request),
    ) -> Response:
        logger.info("Chat request started")
        service: ChatService = request.app.state.chat_service
        try:
            response = await service.dispatch_chat(request, authenticated)
            logger.info("Chat request dispatched")
            return response
        except AgentServiceError as exc:
            logger.warning("Chat request failed (%s)", exc.code)
            raise _http_error(exc) from None
        except Exception as exc:
            logger.exception(
                "Chat request failed type=%s",
                type(exc).__name__,
            )
            raise _http_error(
                AgentServiceError(
                    500,
                    "internal_error",
                    "The assistant encountered an internal error.",
                )
            ) from None

    return application


def _http_error(error: AgentServiceError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.public_message},
    )


app = create_app()
