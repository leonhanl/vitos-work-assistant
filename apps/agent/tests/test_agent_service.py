import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends import StateBackend
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

import work_assistant.agent as agent_module
from work_assistant.agent import AgentService
from work_assistant.auth import AuthenticatedRequest, CurrentUser


def test_agent_service_builds_and_invokes_the_expected_deep_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: test\ndescription: test skill\n---\n", encoding="utf-8")
    settings = SimpleNamespace(skill_file=skill_file)
    captured: dict[str, Any] = {}

    class FakeGraph:
        async def ainvoke(self, state, config):
            captured["state"] = state
            captured["config"] = config
            return {
                "messages": [
                    HumanMessage(content="old question"),
                    AIMessage(content="old answer"),
                    HumanMessage(content="current question"),
                    ToolMessage(
                        name="read_document",
                        tool_call_id="read-1",
                        content=json.dumps(
                            {
                                "name": "VPN KB.docx",
                                "web_url": "https://tenant.example/vpn",
                            }
                        ),
                    ),
                    AIMessage(content="current answer"),
                ]
            }

    def fake_create_deep_agent(**kwargs):
        captured["agent_options"] = kwargs
        return FakeGraph()

    chat_model_client = object()
    monkeypatch.setattr(agent_module, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(
        agent_module,
        "create_chat_model_client",
        lambda unused_settings: chat_model_client,
    )

    class UnusedTokenAcquirer:
        async def acquire_graph_token(self, token_a: str) -> str:
            raise AssertionError("OBO must not run when no MCP tool is invoked")

    service = AgentService(settings, tools=[], token_acquirer=UnusedTokenAcquirer())
    authenticated = AuthenticatedRequest(
        user=CurrentUser(oid="alice-oid", tid="tenant-id"),
        token_a="token-a",
    )
    response = asyncio.run(
        service.chat("thread-1", "current question", authenticated)
    )

    options = captured["agent_options"]
    assert options["model"] is chat_model_client
    assert options["tools"] == []
    assert isinstance(options["backend"], StateBackend)
    assert isinstance(options["checkpointer"], InMemorySaver)
    assert options["skills"] == ["/skills/"]
    assert options["subagents"] == []
    assert options["system_prompt"] == agent_module.SYSTEM_PROMPT
    assert "Always answer in English" in options["system_prompt"]

    assert captured["config"] == {
        "configurable": {"thread_id": "tenant-id:alice-oid:thread-1"}
    }
    assert captured["state"]["messages"] == [
        {"role": "user", "content": "current question"}
    ]
    assert "/skills/enterprise-knowledge-search/SKILL.md" in captured["state"][
        "files"
    ]
    assert response.model_dump() == {
        "thread_id": "thread-1",
        "answer": "current answer",
        "sources": [
            {"name": "VPN KB.docx", "url": "https://tenant.example/vpn"}
        ],
    }
