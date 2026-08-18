"""Pydantic AI agent construction, invocation, and response shaping."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai_harness.skills import Skills

from work_assistant.auth import AuthenticatedRequest
from work_assistant.config import Settings
from work_assistant.llm import create_chat_model_client
from work_assistant.models import ChatResponse, Source
from work_assistant.obo import MCPTokenAcquirer, OboTokenError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Vito's Work Assistant.

Always answer in English. This rule applies regardless of the language used by the
user, earlier messages, tool output, or source documents. Translate or summarize any
non-English source material into English in the final answer.

When a question involves internal company knowledge, IT knowledge-base articles,
company policies, internal processes, operating manuals, or enterprise documents in
Microsoft 365, load the enterprise-knowledge-search capability before answering.
Never invent internal company facts; clearly state when the available material is
insufficient. Do not add a Source, Sources, references section, or document links to
the answer body. The interface separately displays documents that were actually read.

Answer general-knowledge questions directly."""


@dataclass(frozen=True)
class AgentRunDependencies:
    """Authentication data that exists only for one Agent run."""

    token_m: str = field(repr=False)


class AgentServiceError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.public_message = message


class AgentService:
    """One shared Agent with per-user, in-memory conversation history."""

    def __init__(
        self,
        settings: Settings,
        token_acquirer: MCPTokenAcquirer,
        *,
        model: Model | None = None,
    ) -> None:
        self._token_acquirer = token_acquirer
        self._histories: dict[str, list[ModelMessage]] = {}
        self._thread_locks: dict[str, asyncio.Lock] = {}
        self._agent = Agent(
            model or create_chat_model_client(settings),
            deps_type=AgentRunDependencies,
            instructions=SYSTEM_PROMPT,
            capabilities=[Skills(settings.skills_directory)],
        )

        @self._agent.toolset(per_run_step=False)
        def m365_tools(ctx: RunContext[AgentRunDependencies]) -> MCPToolset:
            """Give this run an MCP connection authenticated as its user."""
            headers = None
            if settings.portkey_api_key is not None:
                headers = {
                    "x-portkey-api-key": settings.portkey_api_key.get_secret_value()
                }
            return MCPToolset(
                str(settings.m365_mcp_url),
                auth=ctx.deps.token_m,
                headers=headers,
            )

    async def chat(
        self,
        thread_id: str,
        message: str,
        authenticated: AuthenticatedRequest,
    ) -> ChatResponse:
        internal_thread_id = (
            f"{authenticated.user.tid}:{authenticated.user.oid}:{thread_id}"
        )
        logger.info(
            "Agent execution started",
            extra={"thread_id": thread_id, "user_oid": authenticated.user.oid},
        )

        try:
            token_m = await self._token_acquirer.acquire_mcp_token(
                authenticated.token_a
            )
            lock = self._thread_locks.setdefault(internal_thread_id, asyncio.Lock())
            async with lock:
                result = await self._agent.run(
                    message,
                    deps=AgentRunDependencies(token_m=token_m),
                    message_history=self._histories.get(internal_thread_id),
                )
                self._histories[internal_thread_id] = result.all_messages()

            answer = result.output.strip()
            if not answer:
                raise AgentServiceError(
                    502,
                    "invalid_agent_response",
                    "The configured LLM did not return a usable final answer.",
                )
            return ChatResponse(
                thread_id=thread_id,
                answer=answer,
                sources=normalize_sources(result.new_messages()),
            )
        except OboTokenError as exc:
            if exc.code == "obo_authorization_required":
                raise AgentServiceError(
                    403,
                    "m365_authorization_required",
                    "Microsoft 365 access requires administrator consent or user interaction.",
                ) from exc
            raise AgentServiceError(
                503,
                "m365_authentication_unavailable",
                "Microsoft 365 authentication is temporarily unavailable.",
            ) from exc
        except AgentServiceError:
            raise
        except Exception as exc:
            logger.exception(
                "Agent execution failed type=%s thread_id=%s user_oid=%s",
                type(exc).__name__,
                thread_id,
                authenticated.user.oid,
                extra={
                    "thread_id": thread_id,
                    "user_oid": authenticated.user.oid,
                },
            )
            raise AgentServiceError(
                502,
                "agent_execution_failed",
                "The assistant could not complete this request.",
            ) from exc


def normalize_sources(messages: Iterable[ModelMessage]) -> list[Source]:
    """Extract deduplicated sources from documents actually read this turn."""
    seen: set[tuple[str, str]] = set()
    sources: list[Source] = []

    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if (
                not isinstance(part, ToolReturnPart)
                or part.tool_name != "read_document"
                or not isinstance(part.content, Mapping)
            ):
                continue
            name = part.content.get("name")
            url = part.content.get("web_url")
            if (
                isinstance(name, str)
                and name.strip()
                and isinstance(url, str)
                and url.startswith(("http://", "https://"))
            ):
                key = (name.strip(), url)
                if key not in seen:
                    seen.add(key)
                    sources.append(Source(name=name.strip(), url=url))
    return sources
