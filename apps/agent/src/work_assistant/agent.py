"""DeepAgent construction, invocation, and response shaping."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from deepagents.backends.utils import create_file_data
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from work_assistant.config import Settings
from work_assistant.llm import create_chat_model_client
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
        self._agent_graph = create_deep_agent(
            model=create_chat_model_client(settings),
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
            result = await self._agent_graph.ainvoke(
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
    """截取最后一轮对话：最后一条用户消息，以及它之后的所有消息。

    DeepAgent 配置 checkpointer 后，返回的 ``messages`` 可能包含整个线程的
    历史消息，而不仅仅是当前一轮。例如输入可以表示为：

    ``[用户“VPN 是什么？”, AI“VPN 是……”, 用户“如何安装？”,``
    ``AI 调用 search_sharepoint, 工具返回安装文档, AI“请按以下步骤安装……”]``

    这个函数会返回：

    ``[用户“如何安装？”, AI 调用 search_sharepoint, 工具返回安装文档,``
    ``AI“请按以下步骤安装……”]``

    这样后续提取答案和来源时，就不会误用上一轮的内容。如果消息中没有用户
    消息，则保守地返回原列表。
    """
    # 从末尾向前找，遇到的第一条用户消息就是“最后一轮”的起点。
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        # 优先识别 LangChain 的 HumanMessage，同时兼容只有 type="human"
        # 属性的其他消息实现。
        if isinstance(message, HumanMessage) or getattr(message, "type", None) == "human":
            # 切片包含用户消息本身，以及之后的工具调用、工具结果和最终回答。
            return messages[index:]
    return messages


def _last_answer(messages: Iterable[Any]) -> str:
    """返回消息序列中最后一条非空的 AI 文本回答。

    例如，一轮工具调用可能包含：

    ``[用户“如何安装 VPN？”, AI(content="", 工具调用),``
    ``工具返回安装文档, AI(content="请按以下步骤安装……")]``

    第一条 AI 消息只有工具调用，没有文本内容。函数从后向前搜索并跳过它，最终
    返回 ``"请按以下步骤安装……"``。如果所有 AI 消息都没有可用文本，则抛出
    一个可安全返回给 API 调用方的错误。
    """
    # 从末尾查找，因为最终回答通常是当前轮最后一条 AI 消息。
    for message in reversed(list(messages)):
        # 兼容标准 AIMessage 和其他 type="ai" 的消息实现。
        if isinstance(message, AIMessage) or getattr(message, "type", None) == "ai":
            # 模型 content 既可能是字符串，也可能是文本块列表，统一交给
            # _content_text 转换成字符串。
            text = _content_text(getattr(message, "content", ""))
            if text:
                return text
    # Agent 执行结束却没有产生文本回答时，向上层报告稳定的 502 错误。
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
    """Extract {name, web_url} pairs from the two enterprise tool results, deduped."""
    seen: set[tuple[str, str]] = set()
    sources: list[Source] = []
    for message in messages:
        if getattr(message, "name", None) not in {"search_sharepoint", "read_document"}:
            continue
        for mapping in _iter_mappings(_tool_data(message)):
            name = mapping.get("name")
            url = mapping.get("web_url")
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


def _tool_data(message: Any) -> Any:
    """Return a tool message's structured payload: its artifact or parsed content."""
    artifact = getattr(message, "artifact", None)
    if artifact is not None:
        return artifact

    content = getattr(message, "content", None)
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return None
    return content


def _iter_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    """Yield every mapping nested anywhere inside dicts/lists."""
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _iter_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mappings(child)


def classify_agent_error(exc: Exception) -> AgentServiceError:
    """Collapse dependency failures into stable errors without leaking raw details."""
    detail = str(exc).lower()
    if "login session" in detail or "m365_mcp.auth login" in detail:
        return AgentServiceError(
            503,
            "m365_login_required",
            "Microsoft 365 is not logged in. Run the m365-mcp login command.",
        )
    return AgentServiceError(
        502,
        "agent_execution_failed",
        "The assistant could not complete this request.",
    )
