import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr
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
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai_harness.skills import Skills
from starlette.responses import Response

from work_assistant.agent import (
    AgentRunDependencies,
    AgentService,
    AgentServiceError,
    _portkey_model_settings,
)
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


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        skills_directory=_write_skill_library(tmp_path),
        m365_mcp_url="http://127.0.0.1:9999/mcp",
        jira_mcp_url="http://127.0.0.1:9998/mcp",
        jira_service_desk_id="3",
        portkey_api_key=SecretStr("test-portkey-key"),
    )


def _authenticated(
    oid: str = "alice",
    username: str | None = "alice@example.com",
) -> AuthenticatedRequest:
    return AuthenticatedRequest(
        user=CurrentUser(
            oid=oid,
            tid="tenant",
            username=username,
        ),
        token_a=f"{oid}-token-a",
    )


def test_skills_are_discovered_then_loaded_on_demand(tmp_path: Path) -> None:
    model_calls: list[tuple[list[Any], AgentInfo]] = []

    def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        model_calls.append((list(messages), info))
        if len(model_calls) == 1:
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "test-skill"})]
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


def test_agent_service_exchanges_token_and_dispatches_with_per_run_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TokenAcquirer:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def acquire_mcp_token(self, token_a: str) -> str:
            self.seen.append(token_a)
            return "alice-token-m"

    captured: dict[str, Any] = {}

    async def fake_dispatch(request, **kwargs):
        captured.update(kwargs)
        return Response("stream", media_type="text/event-stream")

    monkeypatch.setattr(AGUIAdapter, "dispatch_request", fake_dispatch)
    token_acquirer = TokenAcquirer()
    service = AgentService(
        _settings(tmp_path),
        token_acquirer,
        model=TestModel(custom_output_text="unused"),
    )

    response = asyncio.run(service.dispatch_chat(SimpleNamespace(), _authenticated()))

    assert response.body == b"stream"
    assert token_acquirer.seen == ["alice-token-a"]
    deps = captured["deps"]
    assert isinstance(deps, AgentRunDependencies)
    assert deps.user_oid == "alice"
    assert deps.username == "alice@example.com"
    assert deps.jira_service_desk_id == "3"
    assert "alice-token-m" not in repr(deps)
    assert captured["agent"] is service._agent
    assert captured["on_complete"] == service._source_events


def test_portkey_headers_track_user_session_and_agent_run() -> None:
    model_settings: list[dict[str, Any]] = []

    def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        model_settings.append(dict(info.model_settings or {}))
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(
        FunctionModel(model_function),
        deps_type=AgentRunDependencies,
        model_settings=_portkey_model_settings,
    )

    def deps(oid: str, username: str | None) -> AgentRunDependencies:
        return AgentRunDependencies(
            token_m="secret-token-m",
            user_oid=oid,
            username=username,
            jira_service_desk_id="3",
        )

    async def run_test() -> None:
        await agent.run(
            "one",
            deps=deps("alice", "alice@example.com"),
            conversation_id="thread-1",
            run_id="run-1",
        )
        await agent.run(
            "two",
            deps=deps("alice", "alice@example.com"),
            conversation_id="thread-1",
            run_id="run-2",
        )
        await agent.run(
            "three",
            deps=deps("bob", "bob@example.com"),
            conversation_id="thread-1",
            run_id="run-3",
        )
        await agent.run(
            "four",
            deps=deps("oid-only", None),
            conversation_id="thread-2",
            run_id="run-4",
        )

    asyncio.run(run_test())

    headers = [settings["extra_headers"] for settings in model_settings]
    assert [header["x-portkey-trace-id"] for header in headers] == [
        "run-1",
        "run-2",
        "run-3",
        "run-4",
    ]
    metadata = [json.loads(header["x-portkey-metadata"]) for header in headers]
    assert metadata[0]["_user"] == "alice@example.com"
    assert metadata[0]["user_oid"] == "alice"
    assert metadata[0]["session_id"] == metadata[1]["session_id"]
    assert metadata[0]["session_id"] != metadata[2]["session_id"]
    assert metadata[3]["_user"] == "oid-only"
    assert set(metadata[0]) == {"_user", "user_oid", "session_id"}
    assert "token" not in headers[0]["x-portkey-metadata"]


def test_agent_service_maps_obo_consent_error(tmp_path: Path) -> None:
    class FailingTokenAcquirer:
        async def acquire_mcp_token(self, token_a: str) -> str:
            raise OboTokenError("obo_authorization_required")

    service = AgentService(
        _settings(tmp_path),
        FailingTokenAcquirer(),
        model=TestModel(custom_output_text="unused"),
    )

    with pytest.raises(AgentServiceError) as raised:
        asyncio.run(service.dispatch_chat(SimpleNamespace(), _authenticated()))

    assert raised.value.status_code == 403
    assert raised.value.code == "m365_authorization_required"


def test_agent_service_logs_unexpected_dispatch_error_with_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class TokenAcquirer:
        async def acquire_mcp_token(self, token_a: str) -> str:
            return "token-m"

    async def failing_dispatch(request, **kwargs):
        raise RuntimeError("gateway rejected request")

    monkeypatch.setattr(AGUIAdapter, "dispatch_request", failing_dispatch)
    service = AgentService(
        _settings(tmp_path),
        TokenAcquirer(),
        model=TestModel(custom_output_text="unused"),
    )

    with caplog.at_level(logging.ERROR, logger="work_assistant.agent"):
        with pytest.raises(AgentServiceError) as raised:
            asyncio.run(service.dispatch_chat(SimpleNamespace(), _authenticated()))

    assert raised.value.code == "agent_execution_failed"
    record = next(
        record
        for record in caplog.records
        if record.name == "work_assistant.agent"
        and record.getMessage()
        == "Agent dispatch failed type=RuntimeError user_oid=alice"
    )
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError
    assert "gateway rejected request" in caplog.text
