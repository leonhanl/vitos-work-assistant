"""Pydantic AI agent construction and AG-UI request dispatch."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from ag_ui.core import BaseEvent, CustomEvent, RunErrorEvent
from pydantic_ai import Agent, ModelHTTPError, RunContext
from pydantic_ai.mcp import CallToolFunc, MCPToolset, ToolResult
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResult
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets import AbstractToolset, ApprovalRequiredToolset
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.ui.ag_ui import AGUIAdapter, AGUIEventStream
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
# Jira tools that take a service desk ID, which the server owns rather than the model.
# Which tools exist at all is decided by the MCP server's ENABLED_TOOLS and, in
# production, by the Prisma AIRS MCP Gateway.
JIRA_SERVICE_DESK_TOOLS = frozenset(
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
    groups: tuple[str, ...] = ()


def _portkey_observability_headers(
    ctx: RunContext[AgentRunDependencies],
) -> dict[str, str]:
    """Build the Portkey identifiers shared by LLM and MCP requests."""
    user = ctx.deps.username or ctx.deps.user_oid
    metadata = {
        "_user": user,
        # Temporary workaround: Portkey's MCP log handler reads `user` while
        # Chat Completions and the documented metadata contract use `_user`.
        "user": user,
        "user_oid": ctx.deps.user_oid,
        "conversation_id": ctx.conversation_id,
        "run_id": ctx.run_id,
        # Metadata values must be strings, so the group array becomes one key each.
        **{f"group_{label}": "true" for label in ctx.deps.groups},
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


RATE_LIMITED = "gateway_rate_limited"
BUDGET_EXHAUSTED = "gateway_budget_exhausted"
EXECUTION_FAILED = "agent_execution_failed"

RUN_ERROR_MESSAGES = {
    # Portkey's 429 carries no Retry-After, but its bucket resets on the wall-clock
    # minute, so waiting a minute is always enough for the per-minute rate limit
    # configured on the service API key.
    RATE_LIMITED: (
        "The AI gateway is rate limiting requests. Please wait a minute and try again."
    ),
    # Unlike a rate limit, an exhausted budget does not recover on its own: it needs an
    # administrator to raise the limit or the policy's periodic reset. So this message
    # must not invite a retry. It also names no amount and no group, because the budget
    # policy is internal: users must not learn another department's limit from an error.
    BUDGET_EXHAUSTED: (
        "The AI assistant's usage budget has been used up. Please contact IT support; "
        "retrying will not help."
    ),
    EXECUTION_FAILED: "The assistant could not complete this request.",
}

# Gateway usage-policy rejections: expected policy outcomes, not application faults.
POLICY_DENIAL_CODES = {429: RATE_LIMITED, 412: BUDGET_EXHAUSTED}

# Doubles as the set of codes logged at WARNING rather than ERROR. A rate limit and an
# exhausted budget are logged apart on purpose: one is a capacity signal, the other a
# budget signal, and they call for different responses.
RUN_ERROR_LOG_SUMMARY = {
    RATE_LIMITED: "AI gateway rate limit hit",
    BUDGET_EXHAUSTED: "AI gateway budget exhausted",
}


@dataclass(frozen=True)
class RunFailure:
    """A failed Agent run described for the client, with no gateway internals."""

    code: str
    message: str
    status_code: int | None = None
    source: str | None = None
    """Which gateway failed, "llm" or "mcp"; they enforce separate rate limits."""


def _policy_denial(status_code: int, source: str) -> RunFailure:
    """Describe a usage-policy rejection, recording which gateway rejected it."""
    code = POLICY_DENIAL_CODES[status_code]
    return RunFailure(code, RUN_ERROR_MESSAGES[code], status_code, source)


def _classify_run_error(error: Exception) -> RunFailure:
    """Describe a failed run for the client without exposing gateway details.

    A usage-policy rejection can surface as a model error or, when the MCP Gateway
    rejects the request, as a transport error that a tool-call wrapper has already
    re-raised from, so the `__cause__` chain is walked rather than just the outermost
    error. MCP requests carry the same `x-portkey-metadata` as model requests, so they
    match the same metadata-scoped policies and see the same rejections.
    """
    exc: BaseException | None = error
    for _ in range(5):
        if exc is None:
            break
        if isinstance(exc, AgentServiceError):
            return RunFailure(exc.code, exc.public_message, exc.status_code)
        if isinstance(exc, ModelHTTPError) and exc.status_code in POLICY_DENIAL_CODES:
            return _policy_denial(exc.status_code, "llm")
        if (
            isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code in POLICY_DENIAL_CODES
        ):
            return _policy_denial(exc.response.status_code, "mcp")
        exc = exc.__cause__
    return RunFailure(EXECUTION_FAILED, RUN_ERROR_MESSAGES[EXECUTION_FAILED])


class _LoggingAGUIEventStream(AGUIEventStream[AgentRunDependencies, Any]):
    """Log why a run failed, and give the client a safe message and a stable code.

    A run failure happens while the AG-UI response is already streaming, so it never
    reaches the exception handlers around `dispatch_chat`; without this the failure is
    never logged and the raw provider error is streamed to the browser.
    """

    async def on_error(self, error: Exception) -> AsyncIterator[BaseEvent]:
        failure = _classify_run_error(error)
        summary = RUN_ERROR_LOG_SUMMARY.get(failure.code)
        logger.log(
            logging.WARNING if summary else logging.ERROR,
            "%s code=%s status=%s source=%s error_type=%s detail=%s",
            summary or "Agent run failed",
            failure.code,
            failure.status_code,
            failure.source,
            type(error).__name__,
            error,
            exc_info=error,
        )
        async for event in super().on_error(error):
            if isinstance(event, RunErrorEvent):
                event = event.model_copy(
                    update={"message": failure.message, "code": failure.code}
                )
            yield event


class _LoggingAGUIAdapter(AGUIAdapter[AgentRunDependencies, Any]):
    """AG-UI adapter whose RUN_ERROR events are logged and sanitized."""

    def build_event_stream(self) -> AGUIEventStream[AgentRunDependencies, Any]:
        return _LoggingAGUIEventStream(
            self.run_input,
            accept=self.accept,
            ag_ui_version=self.ag_ui_version,
        )


def _secure_jira_create_args(
    args: Mapping[str, Any],
    deps: AgentRunDependencies,
) -> dict[str, Any]:
    """Overwrite every trusted Jira create parameter and tidy the drafted fields."""
    if not deps.username:
        raise AgentServiceError(
            403,
            "jira_identity_unavailable",
            "A Jira customer identity could not be determined for the current user.",
        )

    # The MCP tool schema types this as a JSON string, so it must be parsed before
    # the drafted fields can be tidied and re-serialized.
    raw_field_values = args.get("request_field_values")
    try:
        if isinstance(raw_field_values, str):
            raw_field_values = json.loads(raw_field_values)
        request_field_values = dict(raw_field_values)
    except (TypeError, ValueError):
        raise AgentServiceError(
            502,
            "invalid_jira_ticket_draft",
            "The assistant did not produce a valid Jira ticket draft.",
        ) from None

    for field_id in ("summary", "description"):
        value = request_field_values.get(field_id)
        if isinstance(value, str):
            request_field_values[field_id] = value.strip()

    return {
        "service_desk_id": deps.jira_service_desk_id,
        "request_type_id": args.get("request_type_id"),
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
    if name in JIRA_SERVICE_DESK_TOOLS:
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
            return ApprovalRequiredToolset(
                base_toolset,
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
            return await _LoggingAGUIAdapter.dispatch_request(
                request,
                agent=self._agent,
                deps=deps,
                on_complete=self._completion_events,
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
            groups=authenticated.user.groups,
        )

    @staticmethod
    async def _completion_events(
        result: AgentRunResult[Any],
    ) -> AsyncIterator[CustomEvent]:
        yield CustomEvent(
            name="trace",
            value={"trace_id": result.run_id},
        )

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
