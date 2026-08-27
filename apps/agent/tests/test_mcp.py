import asyncio
import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests, ToolApproved, ToolDenied
from starlette.datastructures import Headers

from work_assistant.agent import AgentService, AgentServiceError
from work_assistant.auth import AuthenticatedRequest, CurrentUser


def _free_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


class CaptureHeaders:
    def __init__(self, app) -> None:
        self._app = app
        self.authorization: list[str | None] = []
        self.portkey_api_keys: list[str | None] = []
        self.portkey_trace_ids: list[str | None] = []
        self.portkey_metadata: list[str | None] = []

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] == "/mcp":
            headers = Headers(scope=scope)
            self.authorization.append(headers.get("authorization"))
            self.portkey_api_keys.append(headers.get("x-portkey-api-key"))
            self.portkey_trace_ids.append(headers.get("x-portkey-trace-id"))
            self.portkey_metadata.append(headers.get("x-portkey-metadata"))
        await self._app(scope, receive, send)


@asynccontextmanager
async def _running_servers(*servers_and_ports):
    servers: list[uvicorn.Server] = []
    tasks: list[asyncio.Task] = []
    for app, port in servers_and_ports:
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        servers.append(server)
        tasks.append(asyncio.create_task(server.serve()))

    try:
        async with asyncio.timeout(5):
            while not all(server.started for server in servers):
                await asyncio.sleep(0.01)
        yield
    finally:
        for server in servers:
            server.should_exit = True
        async with asyncio.timeout(5):
            await asyncio.gather(*tasks)


def _write_skill_library(root: Path) -> Path:
    skill = root / "enterprise-knowledge-search"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        """---
name: enterprise-knowledge-search
description: Search enterprise knowledge.
---
Use search_sharepoint.
""",
        encoding="utf-8",
    )
    return root


class TokenAcquirer:
    async def acquire_mcp_token(self, token_a: str) -> str:
        await asyncio.sleep(0)
        return token_a.replace("token-a", "token-m")


def _authenticated(user: str, *, username: str | None = None) -> AuthenticatedRequest:
    return AuthenticatedRequest(
        user=CurrentUser(
            oid=user,
            tid="tenant",
            username=username if username is not None else f"{user}@example.com",
        ),
        token_a=f"{user}-token-a",
    )


async def _run_agent(
    service: AgentService,
    prompt: str,
    authenticated: AuthenticatedRequest,
):
    token_m = await service._token_acquirer.acquire_mcp_token(authenticated.token_a)
    deps = service._run_dependencies(token_m, authenticated)
    return await service._agent.run(prompt, deps=deps)


def test_m365_and_jira_mcp_use_their_distinct_authentication_models(
    tmp_path: Path,
) -> None:
    m365_port, jira_port = _free_ports(2)
    m365_mcp = FastMCP(
        "test-vitos-m365-mcp",
        stateless_http=True,
        json_response=True,
    )
    jira_mcp = FastMCP(
        "test-jira-mcp",
        stateless_http=True,
        json_response=True,
    )

    @m365_mcp.tool()
    async def search_sharepoint(query: str) -> list[dict[str, Any]]:
        return [{"name": f"{query}.docx", "rank": 1}]

    @jira_mcp.tool()
    async def jira_get_request_types(service_desk_id: str) -> list[dict[str, str]]:
        return [{"id": "10", "serviceDeskId": service_desk_id}]

    captured_m365 = CaptureHeaders(m365_mcp.streamable_http_app())
    captured_jira = CaptureHeaders(jira_mcp.streamable_http_app())
    settings = SimpleNamespace(
        skills_directory=_write_skill_library(tmp_path),
        m365_mcp_url=f"http://127.0.0.1:{m365_port}/mcp",
        jira_mcp_url=f"http://127.0.0.1:{jira_port}/mcp",
        jira_service_desk_id="3",
        portkey_api_key=SecretStr("test-portkey-key"),
    )
    service = AgentService(
        settings,
        TokenAcquirer(),
        model=TestModel(call_tools=["search_sharepoint"]),
    )

    async def run_test() -> None:
        async with _running_servers(
            (captured_m365, m365_port),
            (captured_jira, jira_port),
        ):
            await asyncio.gather(
                _run_agent(service, "VPN", _authenticated("alice")),
                _run_agent(service, "VPN", _authenticated("bob")),
            )

    asyncio.run(run_test())

    allowed_m365_headers = {"Bearer alice-token-m", "Bearer bob-token-m"}
    assert captured_m365.authorization
    assert set(captured_m365.authorization) == allowed_m365_headers
    assert captured_jira.authorization
    assert set(captured_jira.authorization) == {None}
    assert set(captured_m365.portkey_api_keys) == {"test-portkey-key"}
    assert set(captured_jira.portkey_api_keys) == {"test-portkey-key"}
    assert None not in captured_m365.portkey_trace_ids
    assert set(captured_m365.portkey_trace_ids) == set(captured_jira.portkey_trace_ids)
    assert len(set(captured_m365.portkey_trace_ids)) == 2
    m365_metadata = {
        json.dumps(json.loads(value), sort_keys=True)
        for value in captured_m365.portkey_metadata
        if value is not None
    }
    jira_metadata = {
        json.dumps(json.loads(value), sort_keys=True)
        for value in captured_jira.portkey_metadata
        if value is not None
    }
    assert m365_metadata == jira_metadata
    assert {
        json.loads(value)["_user"] for value in m365_metadata
    } == {"alice@example.com", "bob@example.com"}
    assert {
        json.loads(value)["user"] for value in m365_metadata
    } == {"alice@example.com", "bob@example.com"}


def test_jira_create_requires_approval_and_forces_trusted_arguments(
    tmp_path: Path,
) -> None:
    m365_port, jira_port = _free_ports(2)
    m365_mcp = FastMCP(
        "test-vitos-m365-mcp",
        stateless_http=True,
        json_response=True,
    )
    jira_mcp = FastMCP(
        "test-jira-mcp",
        stateless_http=True,
        json_response=True,
    )
    created_requests: list[dict[str, Any]] = []

    @m365_mcp.tool()
    async def search_sharepoint(query: str) -> list[dict[str, Any]]:
        return [{"name": f"{query}.docx", "rank": 1}]

    @jira_mcp.tool()
    async def jira_get_request_types(service_desk_id: str) -> list[dict[str, str]]:
        return [{"id": "10", "serviceDeskId": service_desk_id}]

    @jira_mcp.tool()
    async def jira_get_request_type_fields(
        service_desk_id: str,
        request_type_id: str,
    ) -> list[dict[str, str]]:
        return [
            {"fieldId": "summary", "required": "true"},
            {"fieldId": "description", "required": "true"},
        ]

    @jira_mcp.tool()
    async def jira_create_customer_request(
        service_desk_id: str,
        request_type_id: str,
        request_field_values: str,
        raise_on_behalf_of: str | None = None,
        request_participants: str | None = None,
        attachments: str | None = None,
        strict_on_behalf: bool = False,
    ) -> dict[str, str]:
        created_requests.append(
            {
                "service_desk_id": service_desk_id,
                "request_type_id": request_type_id,
                "request_field_values": json.loads(request_field_values),
                "raise_on_behalf_of": raise_on_behalf_of,
                "request_participants": request_participants,
                "attachments": attachments,
                "strict_on_behalf": strict_on_behalf,
            }
        )
        return {"issueKey": "IT-123"}

    def model_function(messages, info) -> ModelResponse:
        jira_returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
            and part.tool_name == "jira_create_customer_request"
        ]
        if jira_returns:
            return ModelResponse(parts=[TextPart(f"Result: {jira_returns[-1].content}")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "jira_create_customer_request",
                    {
                        "service_desk_id": "999",
                        "request_type_id": "10",
                        "request_field_values": json.dumps(
                            {
                                "summary": "  VPN connection fails with error 809  ",
                                "description": "  Troubleshooting did not resolve it.  ",
                            }
                        ),
                        "raise_on_behalf_of": "mallory@example.com",
                        "request_participants": '["mallory@example.com"]',
                        "attachments": "[]",
                        "strict_on_behalf": False,
                    },
                    "jira-call-1",
                )
            ]
        )

    captured_jira = CaptureHeaders(jira_mcp.streamable_http_app())
    settings = SimpleNamespace(
        skills_directory=_write_skill_library(tmp_path),
        m365_mcp_url=f"http://127.0.0.1:{m365_port}/mcp",
        jira_mcp_url=f"http://127.0.0.1:{jira_port}/mcp",
        jira_service_desk_id="3",
        portkey_api_key=SecretStr("test-portkey-key"),
    )
    service = AgentService(
        settings,
        TokenAcquirer(),
        model=FunctionModel(model_function),
    )
    alice = _authenticated("alice")
    bob = _authenticated("bob")

    async def run_test() -> None:
        async with _running_servers(
            (m365_mcp.streamable_http_app(), m365_port),
            (captured_jira, jira_port),
        ):
            pending_result = await _run_agent(service, "Create it", alice)
            assert created_requests == []
            assert isinstance(pending_result.output, DeferredToolRequests)
            assert len(pending_result.output.approvals) == 1
            approval_call = pending_result.output.approvals[0]
            assert approval_call.tool_name == "jira_create_customer_request"

            approved_results = pending_result.output.build_results(
                approvals={approval_call.tool_call_id: ToolApproved()}
            )
            approved = await service._agent.run(
                message_history=pending_result.all_messages(),
                deferred_tool_results=approved_results,
                deps=service._run_dependencies("alice-token-m", alice),
            )
            assert isinstance(approved.output, str)
            assert "IT-123" in approved.output

            denied_result = await _run_agent(service, "Create it", alice)
            assert isinstance(denied_result.output, DeferredToolRequests)
            denied_call = denied_result.output.approvals[0]
            denied_results = denied_result.output.build_results(
                approvals={denied_call.tool_call_id: ToolDenied("Declined")}
            )
            denied = await service._agent.run(
                message_history=denied_result.all_messages(),
                deferred_tool_results=denied_results,
                deps=service._run_dependencies("alice-token-m", alice),
            )
            assert isinstance(denied.output, str)

            with pytest.raises(AgentServiceError) as missing_identity:
                no_identity = _authenticated("no-username", username="")
                missing_result = await _run_agent(service, "Create it", no_identity)
                assert isinstance(missing_result.output, DeferredToolRequests)
                missing_call = missing_result.output.approvals[0]
                await service._agent.run(
                    message_history=missing_result.all_messages(),
                    deferred_tool_results=missing_result.output.build_results(
                        approvals={missing_call.tool_call_id: ToolApproved()}
                    ),
                    deps=service._run_dependencies("no-username-token-m", no_identity),
                )
            assert missing_identity.value.code == "jira_identity_unavailable"

    asyncio.run(run_test())

    assert created_requests == [
        {
            "service_desk_id": "3",
            "request_type_id": "10",
            "request_field_values": {
                "summary": "VPN connection fails with error 809",
                "description": "Troubleshooting did not resolve it.",
            },
            "raise_on_behalf_of": "alice@example.com",
            "request_participants": None,
            "attachments": None,
            "strict_on_behalf": True,
        }
    ]
    assert captured_jira.authorization
    assert set(captured_jira.authorization) == {None}


def test_jira_read_tool_does_not_require_approval(tmp_path: Path) -> None:
    m365_port, jira_port = _free_ports(2)
    m365_mcp = FastMCP("test-m365", stateless_http=True, json_response=True)
    jira_mcp = FastMCP("test-jira", stateless_http=True, json_response=True)
    reads: list[str] = []

    @m365_mcp.tool()
    async def search_sharepoint(query: str) -> list[dict[str, str]]:
        return [{"name": query}]

    @jira_mcp.tool()
    async def jira_get_request_types(service_desk_id: str) -> list[dict[str, str]]:
        reads.append(service_desk_id)
        return [{"id": "10"}]

    def model_function(messages, info) -> ModelResponse:
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
            and part.tool_name == "jira_get_request_types"
        ]
        if returns:
            return ModelResponse(parts=[TextPart("Found the request type")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "jira_get_request_types",
                    {"service_desk_id": "999"},
                    "jira-read-1",
                )
            ]
        )

    settings = SimpleNamespace(
        skills_directory=_write_skill_library(tmp_path),
        m365_mcp_url=f"http://127.0.0.1:{m365_port}/mcp",
        jira_mcp_url=f"http://127.0.0.1:{jira_port}/mcp",
        jira_service_desk_id="3",
        portkey_api_key=SecretStr("test-portkey-key"),
    )
    service = AgentService(settings, TokenAcquirer(), model=FunctionModel(model_function))

    async def run_test() -> None:
        async with _running_servers(
            (m365_mcp.streamable_http_app(), m365_port),
            (jira_mcp.streamable_http_app(), jira_port),
        ):
            response = await _run_agent(
                service,
                "List types",
                _authenticated("alice"),
            )
            assert response.output == "Found the request type"

    asyncio.run(run_test())
    assert reads == ["3"]
