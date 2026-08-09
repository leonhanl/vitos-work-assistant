"""FastAPI entry point for Vito's Work Assistant."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request

from work_assistant.agent import AgentService, AgentServiceError
from work_assistant.config import Settings
from work_assistant.mcp import M365MCPClient, MCPConnectionError
from work_assistant.models import ChatRequest, ChatResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


class ChatService(Protocol):
    async def chat(self, thread_id: str, message: str) -> ChatResponse: ...


ServiceFactory = Callable[[], AbstractAsyncContextManager[ChatService]]


@asynccontextmanager
async def agent_service_context() -> AsyncIterator[ChatService]:
    settings = Settings()
    mcp_client = M365MCPClient(settings)
    try:
        tools = await mcp_client.connect()
        yield AgentService(settings, tools)
    finally:
        await mcp_client.close()


def create_app(service_factory: ServiceFactory = agent_service_context) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.chat_service = None
        application.state.startup_error = None
        context = service_factory()
        try:
            service = await context.__aenter__()
        except Exception as exc:
            error = _startup_error(exc)
            application.state.startup_error = error
            logger.error("Agent startup failed (%s)", type(exc).__name__)
            yield
        else:
            application.state.chat_service = service
            try:
                yield
            finally:
                application.state.chat_service = None
                await context.__aexit__(None, None, None)

    application = FastAPI(
        title="Vito's Work Assistant API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        thread_id = payload.thread_id or str(uuid4())
        logger.info("Chat request started", extra={"thread_id": thread_id})
        service: ChatService | None = request.app.state.chat_service
        if service is None:
            error: AgentServiceError = request.app.state.startup_error
            logger.warning("Chat request rejected: service unavailable")
            raise _http_error(error)
        try:
            response = await service.chat(thread_id, payload.message)
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


def _startup_error(exc: Exception) -> AgentServiceError:
    if isinstance(exc, AgentServiceError):
        return exc
    if isinstance(exc, MCPConnectionError):
        return AgentServiceError(
            503,
            "mcp_unavailable",
            "The Microsoft 365 knowledge service could not be started.",
        )
    if type(exc).__name__ == "ValidationError":
        return AgentServiceError(
            503,
            "configuration_error",
            "The Agent API is missing or has invalid LLM configuration.",
        )
    return AgentServiceError(
        503,
        "service_unavailable",
        "The Agent API could not be initialized.",
    )


def _http_error(error: AgentServiceError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.public_message},
    )


app = create_app()

