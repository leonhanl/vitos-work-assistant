"""DeepAgent construction, invocation, and response shaping."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from deepagents.backends.utils import create_file_data
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from work_assistant.config import Settings
from work_assistant.llm import create_chat_model
from work_assistant.models import ChatResponse, Source

logger = logging.getLogger(__name__)

# A ChatOpenAI instance reports its LangChain provider as "openai" even when
# base_url targets another compatible endpoint. This profile keeps the demo a
# single Agent and gives it read-only access to its in-memory Skill files.
register_harness_profile(
    "openai",
    HarnessProfile(
        excluded_tools=frozenset({"write_file", "edit_file"}),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)

SYSTEM_PROMPT = """你是 Vito's Work Assistant。

当问题涉及公司内部知识、IT KB、公司政策、内部流程、操作手册或 Microsoft 365
中的企业文档时，优先使用可用的企业知识检索能力获取事实依据。不要编造企业内部
事实；资料不足时明确说明。回答企业知识问题时，引用工具实际返回的文档名称和链接。
普通常识问题可以直接回答。"""


class AgentServiceError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.public_message = message


class AgentService:
    """One process-local DeepAgent with in-memory threaded conversations."""

    def __init__(self, settings: Settings, tools: Sequence[BaseTool]) -> None:
        skill_text = settings.skill_file.read_text(encoding="utf-8")
        self._skill_files = {
            "/skills/enterprise-knowledge-search/SKILL.md": create_file_data(
                skill_text
            )
        }
        self._graph = create_deep_agent(
            model=create_chat_model(settings),
            tools=list(tools),
            system_prompt=SYSTEM_PROMPT,
            backend=StateBackend(),
            skills=["/skills/"],
            subagents=[],
            checkpointer=InMemorySaver(),
        )

    async def chat(self, thread_id: str, message: str) -> ChatResponse:
        logger.info("Agent execution started", extra={"thread_id": thread_id})
        try:
            result = await self._graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": message}],
                    "files": self._skill_files,
                },
                config={"configurable": {"thread_id": thread_id}},
            )
            messages = result.get("messages")
            if not isinstance(messages, list):
                raise RuntimeError("DeepAgent returned no messages")
            current_turn = _messages_from_last_user(messages)
            answer = _last_answer(current_turn)
            sources = normalize_sources(current_turn)
            return ChatResponse(thread_id=thread_id, answer=answer, sources=sources)
        except AgentServiceError:
            raise
        except Exception as exc:
            raise classify_agent_error(exc) from exc


def _messages_from_last_user(messages: list[Any]) -> list[Any]:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, HumanMessage) or getattr(message, "type", None) == "human":
            return messages[index:]
    return messages


def _last_answer(messages: Iterable[Any]) -> str:
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage) or getattr(message, "type", None) == "ai":
            text = _content_text(getattr(message, "content", ""))
            if text:
                return text
    raise AgentServiceError(
        502,
        "invalid_agent_response",
        "The configured LLM did not return a usable final answer.",
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def normalize_sources(messages: Iterable[Any]) -> list[Source]:
    """Extract only structured URLs returned by the two real MCP tools."""
    read_sources: list[Source] = []
    search_sources: list[Source] = []
    for message in messages:
        if not isinstance(message, ToolMessage) and getattr(message, "type", None) != "tool":
            continue
        name = getattr(message, "name", None)
        if name not in {"search_sharepoint", "read_document"}:
            continue
        destination = read_sources if name == "read_document" else search_sources
        for payload in _tool_payloads(message):
            for candidate in _walk_mappings(payload):
                source = _mapping_to_source(candidate)
                if source is not None:
                    destination.append(source)
    return _deduplicate_sources(read_sources or search_sources)


def _tool_payloads(message: Any) -> Iterable[Any]:
    artifact = getattr(message, "artifact", None)
    if artifact is not None:
        yield artifact

    content = getattr(message, "content", None)
    if isinstance(content, str):
        parsed = _parse_json(content)
        if parsed is not None:
            yield parsed
        return
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping):
                text = block.get("text")
                parsed = _parse_json(text) if isinstance(text, str) else None
                yield parsed if parsed is not None else block


def _parse_json(value: str) -> Any | None:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _mapping_to_source(value: Mapping[str, Any]) -> Source | None:
    name = value.get("name")
    url = value.get("web_url")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(url, str) or not _is_http_url(url):
        return None
    return Source(name=name.strip(), url=url)


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _deduplicate_sources(sources: Iterable[Source]) -> list[Source]:
    result: list[Source] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        key = (source.name, source.url)
        if key not in seen:
            seen.add(key)
            result.append(source)
    return result


def classify_agent_error(exc: Exception) -> AgentServiceError:
    """Map dependency failures to stable messages without returning raw details."""
    name = type(exc).__name__.lower()
    detail = str(exc).lower()

    if "no valid microsoft 365 login session" in detail or "m365_mcp.auth login" in detail:
        return AgentServiceError(
            503,
            "m365_login_required",
            "Microsoft 365 is not logged in. Run the m365-mcp-http login command.",
        )
    if "microsoft graph" in detail and ("401" in detail or "login session" in detail):
        return AgentServiceError(
            503,
            "m365_authentication_failed",
            "Microsoft 365 rejected the local login session. Log in again and retry.",
        )
    if "microsoft graph" in detail and "403" in detail:
        return AgentServiceError(
            502,
            "m365_permission_denied",
            "Microsoft 365 denied access. Check delegated consent and SharePoint permissions.",
        )
    if "microsoft graph" in detail and "429" in detail:
        return AgentServiceError(
            503,
            "m365_rate_limited",
            "Microsoft 365 is throttling requests. Wait briefly and retry.",
        )
    if "microsoft graph" in detail and "404" in detail:
        return AgentServiceError(
            502,
            "m365_document_not_found",
            "The selected Microsoft 365 document no longer exists or is unavailable.",
        )
    if "microsoft graph" in detail and "could not connect" in detail:
        return AgentServiceError(
            503,
            "m365_unreachable",
            "Microsoft Graph is unreachable. Check the network and retry.",
        )
    if "unsupported" in detail or "does not support" in detail:
        return AgentServiceError(
            502,
            "m365_document_read_failed",
            "The selected enterprise document could not be read in its current format.",
        )
    if "toolexecution" in name:
        return AgentServiceError(
            502,
            "enterprise_tool_failed",
            "The enterprise knowledge tool could not complete its operation.",
        )
    if "mcp" in name or "session" in name or "transport" in name:
        return AgentServiceError(
            503,
            "mcp_unavailable",
            "The Microsoft 365 knowledge service is unavailable.",
        )
    if "authentication" in name or "permissiondenied" in name:
        return AgentServiceError(
            502,
            "llm_authentication_failed",
            "The LLM provider rejected its credentials.",
        )
    if "ratelimit" in name:
        return AgentServiceError(
            503,
            "llm_rate_limited",
            "The LLM provider is rate limiting requests. Retry later.",
        )
    if "notfound" in name or ("model" in detail and "not found" in detail):
        return AgentServiceError(
            502,
            "llm_model_not_found",
            "The configured LLM model was not found by the provider.",
        )
    if "function tools with reasoning_effort" in detail:
        return AgentServiceError(
            502,
            "llm_tool_calling_configuration_error",
            "This model requires reasoning effort 'none' for function tools over Chat Completions.",
        )
    if "tool" in detail and ("support" in detail or "function" in detail):
        return AgentServiceError(
            502,
            "llm_tool_calling_unsupported",
            "The configured LLM provider or model does not support required tool calling.",
        )
    if "connection" in name or "timeout" in name:
        return AgentServiceError(
            502,
            "llm_unreachable",
            "The configured LLM endpoint is unreachable.",
        )
    return AgentServiceError(
        502,
        "agent_execution_failed",
        "The assistant could not complete this request.",
    )
