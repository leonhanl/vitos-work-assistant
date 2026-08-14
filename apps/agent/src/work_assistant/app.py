"""FastAPI entry point for Vito's Work Assistant."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Protocol
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request

from work_assistant.agent import AgentService, AgentServiceError
from work_assistant.auth import (
    AuthenticatedRequest,
    CurrentUser,
    get_authenticated_request,
    get_current_user,
)
from work_assistant.config import Settings
from work_assistant.mcp import M365MCPClient
from work_assistant.models import ChatRequest, ChatResponse
from work_assistant.obo import OboTokenService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


class ChatService(Protocol):
    """The one method FastAPI needs from the Agent service."""

    async def chat(
        self,
        thread_id: str,
        message: str,
        authenticated: AuthenticatedRequest,
    ) -> ChatResponse: ...


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
        mcp_client = M365MCPClient(settings)
        try:
            tools = await mcp_client.connect()
            application.state.chat_service = AgentService(
                settings,
                tools,
                OboTokenService(settings),
            )
            yield
        finally:
            application.state.chat_service = None
            await mcp_client.close()

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

    @application.post("/chat", response_model=ChatResponse)
    async def chat(
        payload: ChatRequest,
        request: Request,
        authenticated: AuthenticatedRequest = Depends(get_authenticated_request),
    ) -> ChatResponse:
        thread_id = payload.thread_id or str(uuid4())
        logger.info("Chat request started", extra={"thread_id": thread_id})
        service: ChatService = request.app.state.chat_service
        try:
            response = await service.chat(thread_id, payload.message, authenticated)
            logger.info("Chat request succeeded", extra={"thread_id": thread_id})
            return response
        except AgentServiceError as exc:
            logger.warning(
                "Chat request failed (%s)",
                exc.code,
                extra={"thread_id": thread_id},
            )
            raise _http_error(exc) from None
        except Exception:
            logger.error(
                "Chat request failed (unexpected service error)",
                extra={"thread_id": thread_id},
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
