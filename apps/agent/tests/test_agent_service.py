import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.skills import Skills

from work_assistant.agent import AgentRunDependencies, AgentService, AgentServiceError
from work_assistant.auth import AuthenticatedRequest, CurrentUser
from work_assistant.obo import OboTokenError


def _write_skill_library(root: Path) -> Path:
    skill_directory = root / "test-skill"
    skill_directory.mkdir()
    (skill_directory / "SKILL.md").write_text(
        """---
name: test-skill
description: Use this test skill for internal questions.
---

# Secret test instructions

Search internal material before answering.
""",
        encoding="utf-8",
    )
    return root


def test_skills_are_discovered_then_loaded_on_demand(tmp_path: Path) -> None:
    model_calls: list[tuple[list[Any], AgentInfo]] = []

    def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        model_calls.append((list(messages), info))
        if len(model_calls) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "load_capability",
                        {"id": "test-skill"},
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(
        FunctionModel(model_function),
        capabilities=[Skills(_write_skill_library(tmp_path))],
    )
    result = asyncio.run(agent.run("Find the policy"))

    first_instructions = model_calls[0][1].instructions or ""
    assert "test-skill" in first_instructions
    assert "Secret test instructions" not in first_instructions

    second_messages = model_calls[1][0]
    loaded = [
        part.content
        for message in second_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
        and part.tool_name == "load_capability"
    ]
    assert "Secret test instructions" in str(loaded)
    assert result.output == "done"


def test_agent_service_exchanges_token_and_isolates_history_by_user(
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        skills_directory=_write_skill_library(tmp_path),
        m365_mcp_url="http://127.0.0.1:9999/mcp",
    )

    class TokenAcquirer:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def acquire_mcp_token(self, token_a: str) -> str:
            self.seen.append(token_a)
            return token_a.replace("token-a", "token-m")

    class FakeResult:
        output = "current answer"

        def all_messages(self):
            return [ModelRequest(parts=[])]

        def new_messages(self):
            return [
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            "read_document",
                            {
                                "name": "VPN KB.docx",
                                "web_url": "https://tenant.example/vpn",
                            },
                        )
                    ]
                )
            ]

    class FakeAgent:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run(self, message: str, **kwargs: Any) -> FakeResult:
            self.calls.append({"message": message, **kwargs})
            return FakeResult()

    token_acquirer = TokenAcquirer()
    service = AgentService(
        settings,
        token_acquirer,
        model=TestModel(custom_output_text="unused"),
    )
    fake_agent = FakeAgent()
    service._agent = fake_agent  # type: ignore[assignment]

    alice = AuthenticatedRequest(
        user=CurrentUser(oid="alice", tid="tenant"),
        token_a="alice-token-a",
    )
    bob = AuthenticatedRequest(
        user=CurrentUser(oid="bob", tid="tenant"),
        token_a="bob-token-a",
    )

    first = asyncio.run(service.chat("thread-1", "first", alice))
    asyncio.run(service.chat("thread-1", "second", alice))
    asyncio.run(service.chat("thread-1", "first", bob))

    assert token_acquirer.seen == [
        "alice-token-a",
        "alice-token-a",
        "bob-token-a",
    ]
    assert isinstance(fake_agent.calls[0]["deps"], AgentRunDependencies)
    assert "alice-token-m" not in repr(fake_agent.calls[0]["deps"])
    assert fake_agent.calls[0]["message_history"] is None
    assert fake_agent.calls[1]["message_history"] is not None
    assert fake_agent.calls[2]["message_history"] is None
    assert first.model_dump() == {
        "thread_id": "thread-1",
        "answer": "current answer",
        "sources": [
            {"name": "VPN KB.docx", "url": "https://tenant.example/vpn"}
        ],
    }


def test_agent_service_maps_obo_consent_error(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        skills_directory=_write_skill_library(tmp_path),
        m365_mcp_url="http://127.0.0.1:9999/mcp",
    )

    class FailingTokenAcquirer:
        async def acquire_mcp_token(self, token_a: str) -> str:
            raise OboTokenError("obo_authorization_required")

    service = AgentService(
        settings,
        FailingTokenAcquirer(),
        model=TestModel(custom_output_text="unused"),
    )
    authenticated = AuthenticatedRequest(
        user=CurrentUser(oid="alice", tid="tenant"),
        token_a="alice-token-a",
    )

    try:
        asyncio.run(service.chat("thread-1", "hello", authenticated))
    except AgentServiceError as exc:
        assert exc.status_code == 403
        assert exc.code == "m365_authorization_required"
    else:
        raise AssertionError("Expected AgentServiceError")
