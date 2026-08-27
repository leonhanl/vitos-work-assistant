"""Pydantic AI agent construction and AG-UI request dispatch."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from ag_ui.core import CustomEvent
from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import CallToolFunc, MCPToolset, ToolResult
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResult
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets import (
    AbstractToolset,
    ApprovalRequiredToolset,
    FilteredToolset,
)
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai_harness.skills import Skills
from starlette.requests import Request
from starlette.responses import Response

from work_assistant.auth import AuthenticatedRequest
from work_assistant.config import Settings
from work_assistant.llm import create_chat_model_client
from work_assistant.models import Source
from work_assistant.obo import MCPTokenAcquirer, OboTokenError

logger = logging.getLogger(__name__)

JIRA_CREATE_TOOL = "jira_create_customer_request"
ALLOWED_JIRA_TOOLS = frozenset(
    {
        "jira_get_request_types",
        "jira_get_request_type_fields",
        JIRA_CREATE_TOOL,
    }
)

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

When IT troubleshooting has not resolved the problem and the user wants help from the
IT team, load the it-support-case-creation capability. Follow it to prepare a complete
Jira Service Management customer request. Never invent request types or required field
values. The application will require explicit approval before the create tool runs.
Only state that a ticket was created after the tool returns a successful result.

Answer general-knowledge questions directly."""


@dataclass(frozen=True)
class AgentRunDependencies:
    """Trusted data that exists only for one Agent run."""

    token_m: str = field(repr=False)
    user_oid: str
    username: str | None
    jira_service_desk_id: str


def _portkey_observability_headers(
    ctx: RunContext[AgentRunDependencies],
) -> dict[str, str]:
    """Build the Portkey identifiers shared by LLM and MCP requests."""
    session_id = sha256(
        f"{ctx.deps.user_oid}\0{ctx.conversation_id}".encode()
    ).hexdigest()
    user = ctx.deps.username or ctx.deps.user_oid
    metadata = {
        "_user": user,
        # Temporary workaround: Portkey's MCP log handler reads `user` while
        # Chat Completions and the documented metadata contract use `_user`.
        "user": user,
        "user_oid": ctx.deps.user_oid,
        "session_id": session_id,
    }
    return {
        "x-portkey-trace-id": ctx.run_id,
        "x-portkey-metadata": json.dumps(
            metadata,
            separators=(",", ":"),
            sort_keys=True,
        ),
    }


def _portkey_model_settings(
    ctx: RunContext[AgentRunDependencies],
) -> ModelSettings:
    """Attach Portkey observability headers to every model request."""
    return ModelSettings(
        extra_headers=_portkey_observability_headers(ctx),
    )


class AgentServiceError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.public_message = message


class JiraToolCallError(RuntimeError):
    """A Jira MCP tool call failed without exposing its private details."""


def _secure_jira_create_args(
    args: Mapping[str, Any],
    deps: AgentRunDependencies,
) -> dict[str, Any]:
    """Validate a Jira draft and overwrite every trusted create parameter."""
    if not deps.username:
        raise AgentServiceError(
            403,
            "jira_identity_unavailable",
            "A Jira customer identity could not be determined for the current user.",
        )

    request_type_id = str(args.get("request_type_id", "")).strip()
    if not request_type_id:
        raise AgentServiceError(
            502,
            "invalid_jira_ticket_draft",
            "The assistant did not select a valid Jira request type.",
        )

    raw_field_values = args.get("request_field_values")
    try:
        if isinstance(raw_field_values, str):
            request_field_values = json.loads(raw_field_values)
        elif isinstance(raw_field_values, Mapping):
            request_field_values = dict(raw_field_values)
        else:
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError):
        raise AgentServiceError(
            502,
            "invalid_jira_ticket_draft",
            "The assistant did not produce valid Jira request fields.",
        ) from None

    if not isinstance(request_field_values, dict):
        raise AgentServiceError(
            502,
            "invalid_jira_ticket_draft",
            "The assistant did not produce valid Jira request fields.",
        )

    summary = request_field_values.get("summary")
    description = request_field_values.get("description")
    if not isinstance(summary, str) or not summary.strip():
        raise AgentServiceError(
            502,
            "invalid_jira_ticket_draft",
            "The Jira ticket draft is missing a summary.",
        )
    if not isinstance(description, str) or not description.strip():
        raise AgentServiceError(
            502,
            "invalid_jira_ticket_draft",
            "The Jira ticket draft is missing a description.",
        )

    request_field_values["summary"] = summary.strip()
    request_field_values["description"] = description.strip()
    return {
        "service_desk_id": deps.jira_service_desk_id,
        "request_type_id": request_type_id,
        "request_field_values": json.dumps(
            request_field_values,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "raise_on_behalf_of": deps.username,
        "strict_on_behalf": True,
    }


async def _process_jira_tool_call(
    ctx: RunContext[AgentRunDependencies],
    call_tool: CallToolFunc,
    name: str,
    args: dict[str, Any],
) -> ToolResult:
    """Apply Jira policy immediately before an MCP request leaves the Agent."""
    if name in ALLOWED_JIRA_TOOLS:
        args = {**args, "service_desk_id": ctx.deps.jira_service_desk_id}
    if name == JIRA_CREATE_TOOL:
        args = _secure_jira_create_args(args, ctx.deps)

    try:
        return await call_tool(name, args)
    except AgentServiceError:
        raise
    except Exception as exc:
        raise JiraToolCallError(
            "Jira could not complete the requested operation."
        ) from exc


class AgentService:
    """One shared Agent dispatched through Pydantic AI's AG-UI adapter."""

    def __init__(
        self,
        settings: Settings,
        token_acquirer: MCPTokenAcquirer,
        *,
        model: Model | None = None,
    ) -> None:
        self._token_acquirer = token_acquirer
        self._jira_service_desk_id = settings.jira_service_desk_id
        portkey_api_key = settings.portkey_api_key.get_secret_value()
        self._agent = Agent(
            model or create_chat_model_client(settings),
            deps_type=AgentRunDependencies,
            output_type=[str, DeferredToolRequests],
            instructions=SYSTEM_PROMPT,
            model_settings=_portkey_model_settings,
            capabilities=[Skills(settings.skills_directory)],
        )

        @self._agent.toolset(per_run_step=False)
        def m365_tools(ctx: RunContext[AgentRunDependencies]) -> MCPToolset:
            """Give this run an M365 MCP connection authenticated as its user."""
            return MCPToolset(
                str(settings.m365_mcp_url),
                auth=ctx.deps.token_m,
                headers={
                    "x-portkey-api-key": portkey_api_key,
                    **_portkey_observability_headers(ctx),
                },
            )

        @self._agent.toolset(per_run_step=False)
        def jira_tools(
            ctx: RunContext[AgentRunDependencies],
        ) -> AbstractToolset[AgentRunDependencies]:
            """Give this run a policy-constrained Jira MCP connection."""
            base_toolset = MCPToolset(
                str(settings.jira_mcp_url),
                id="jira-service-desk",
                headers={
                    "x-portkey-api-key": portkey_api_key,
                    **_portkey_observability_headers(ctx),
                },
                max_retries=0,
                tool_error_behavior="error",
                process_tool_call=_process_jira_tool_call,
            )
            allowed_toolset = FilteredToolset(
                base_toolset,
                lambda run_ctx, tool: tool.name in ALLOWED_JIRA_TOOLS,
            )
            return ApprovalRequiredToolset(
                allowed_toolset,
                lambda run_ctx, tool, args: tool.name == JIRA_CREATE_TOOL,
            )

    async def dispatch_chat(
        self,
        request: Request,
        authenticated: AuthenticatedRequest,
    ) -> Response:
        """Dispatch one AG-UI run; the protocol owns history and approval resume."""
        logger.info(
            "Agent execution started",
            extra={"user_oid": authenticated.user.oid},
        )
        try:
            token_m = await self._token_acquirer.acquire_mcp_token(
                authenticated.token_a
            )
            deps = self._run_dependencies(token_m, authenticated)
            return await AGUIAdapter.dispatch_request(
                request,
                agent=self._agent,
                deps=deps,
                on_complete=self._source_events,
            )
        except OboTokenError as exc:
            raise self._map_obo_error(exc) from exc
        except AgentServiceError:
            raise
        except Exception as exc:
            logger.exception(
                "Agent dispatch failed type=%s user_oid=%s",
                type(exc).__name__,
                authenticated.user.oid,
                extra={"user_oid": authenticated.user.oid},
            )
            raise AgentServiceError(
                502,
                "agent_execution_failed",
                "The assistant could not complete this request.",
            ) from exc

    def _run_dependencies(
        self,
        token_m: str,
        authenticated: AuthenticatedRequest,
    ) -> AgentRunDependencies:
        return AgentRunDependencies(
            token_m=token_m,
            user_oid=authenticated.user.oid,
            username=authenticated.user.username,
            jira_service_desk_id=self._jira_service_desk_id,
        )

    @staticmethod
    async def _source_events(
        result: AgentRunResult[Any],
    ) -> AsyncIterator[CustomEvent]:
        sources = normalize_sources(result.new_messages())
        if sources:
            yield CustomEvent(
                name="sources",
                value=[source.model_dump(mode="json") for source in sources],
            )

    @staticmethod
    def _map_obo_error(exc: OboTokenError) -> AgentServiceError:
        if exc.code == "obo_authorization_required":
            return AgentServiceError(
                403,
                "m365_authorization_required",
                "Microsoft 365 access requires administrator consent or user interaction.",
            )
        return AgentServiceError(
            503,
            "m365_authentication_unavailable",
            "Microsoft 365 authentication is temporarily unavailable.",
        )


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
