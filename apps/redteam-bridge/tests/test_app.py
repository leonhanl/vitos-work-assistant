import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from redteam_bridge.app import create_app
from redteam_bridge.errors import BridgeError


class Tokens:
    def get_token(self):
        return "alice-token-a"


def event(kind, **fields):
    return {"type": kind, **fields}


def answer(text="Answer", **finish):
    return [
        event("TEXT_MESSAGE_START", messageId="a", role="assistant"),
        event("TEXT_MESSAGE_CONTENT", messageId="a", delta=text),
        event("TEXT_MESSAGE_END", messageId="a"),
        event("CUSTOM", name="trace", value={"trace_id": "trace-123"}),
        event("RUN_FINISHED", outcome={"type": "success"}, **finish),
    ]


def sse(events):
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        text=": keepalive\n\n" + "".join("data: " + json.dumps(e) + "\n\n" for e in events),
    )


@contextmanager
def api(settings, handler, tokens=None):
    with httpx.Client(transport=httpx.MockTransport(handler)) as upstream:
        with TestClient(create_app(settings, tokens or Tokens(), upstream)) as client:
            client.headers["Authorization"] = f"Bearer {settings.api_key.get_secret_value()}"
            yield client


def test_api_key_and_input_boundaries(settings):
    requests = []
    with api(settings, lambda request: requests.append(request) or sse(answer())) as client:
        assert client.get("/health", headers={"Authorization": ""}).status_code == 200
        for credentials in ("", "Bearer wrong-key", "Basic invalid"):
            response = client.post("/redteam/chat", json={"prompt": "hello"}, headers={"Authorization": credentials})
            assert response.status_code == 401
        for body in ({"prompt": "  "}, {"prompt": "hello", "resume": []}, {"prompt": "hello", "user_oid": "bob"}):
            assert client.post("/redteam/chat", json=body).status_code == 422
    assert not requests


def test_rest_response_and_user_token_forwarding(settings):
    def upstream(request):
        assert request.headers["Authorization"] == "Bearer alice-token-a"
        assert settings.api_key.get_secret_value() not in str(request.headers)
        body = json.loads(request.content)
        assert body["messages"][0]["content"] == "hello"
        assert body["tools"] == []
        assert body["forwardedProps"] == {"guardrails_enabled": False}
        assert body["threadId"] and body["runId"]
        return sse(answer("Hello Alice"))

    with api(settings, upstream) as client:
        response = client.post("/redteam/chat", json={"prompt": "hello"})
    assert response.status_code == 200
    result = response.json()
    assert result["response"] == "Hello Alice"
    assert result["status"] == "completed"
    assert result["trace_id"] == "trace-123"
    assert result["interrupts"] == []
    assert "token" not in response.text


@pytest.mark.parametrize("enabled", [True, False])
def test_guardrail_mode_is_forwarded(settings, enabled):
    def upstream(request):
        body = json.loads(request.content)
        assert body["forwardedProps"] == {"guardrails_enabled": enabled}
        return sse(answer())

    with api(settings, upstream) as client:
        response = client.post("/redteam/chat", json={
            "prompt": "hello", "guardrails_enabled": enabled,
        })
    assert response.status_code == 200


@pytest.mark.parametrize("invalid_mode", ["false", 0, None])
def test_guardrail_mode_requires_a_boolean(settings, invalid_mode):
    calls = []
    with api(settings, lambda request: calls.append(request) or sse(answer())) as client:
        response = client.post("/redteam/chat", json={
            "prompt": "hello", "guardrails_enabled": invalid_mode,
        })
    assert response.status_code == 422
    assert not calls


def test_tool_history_metadata_and_state_survive_restart(settings):
    bodies = []

    def upstream(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return sse([
                event("TOOL_CALL_START", toolCallId="tool-1", toolCallName="read_document", parentMessageId="tools"),
                event("TOOL_CALL_ARGS", toolCallId="tool-1", delta='{"id":'),
                event("TOOL_CALL_ARGS", toolCallId="tool-1", delta='"doc-1"}'),
                event("REASONING_ENCRYPTED_VALUE", subtype="tool-call", entityId="tool-1", encryptedValue="tool-kind"),
                event("TOOL_CALL_END", toolCallId="tool-1"),
                event("TOOL_CALL_RESULT", toolCallId="tool-1", messageId="result", content="Internal policy"),
                event("REASONING_ENCRYPTED_VALUE", subtype="message", entityId="result", encryptedValue="tool-outcome"),
                event("ACTIVITY_SNAPSHOT", messageId="activity", activityType="pydantic_ai_tool_availability_delta", content={"added": []}),
                event("STATE_SNAPSHOT", snapshot={"value": 1}),
                *answer("Policy answer"),
            ])
        # Avoid reusing an assistant ID across turns, as the actual SDK does.
        events = answer("Follow-up answer")
        for item in events:
            if "messageId" in item:
                item["messageId"] = "a2"
        return sse(events)

    with api(settings, upstream) as client:
        first = client.post("/redteam/chat", json={"prompt": "Find the policy"}).json()
    with api(settings, upstream) as client:
        second = client.post("/redteam/chat", json={"prompt": "Explain it", "conversation_id": first["conversation_id"]})
    assert second.status_code == 200
    history = bodies[1]["messages"]
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "activity", "assistant", "user"]
    assert history[1]["toolCalls"][0]["function"]["arguments"] == '{"id":"doc-1"}'
    assert history[1]["toolCalls"][0]["encryptedValue"] == "tool-kind"
    assert history[2]["content"] == "Internal policy"
    assert history[2]["encryptedValue"] == "tool-outcome"
    assert bodies[1]["state"] == {"value": 1}
    assert bodies[1]["threadId"] == bodies[0]["threadId"]
    assert bodies[1]["runId"] != bodies[0]["runId"]


def test_conversations_are_isolated_and_unknown_ids_do_not_create_sessions(settings):
    bodies = []
    with api(settings, lambda request: bodies.append(json.loads(request.content)) or sse(answer())) as client:
        one = client.post("/redteam/chat", json={"prompt": "first"}).json()
        two = client.post("/redteam/chat", json={"prompt": "second"}).json()
        assert one["conversation_id"] != two["conversation_id"]
        assert len(bodies[1]["messages"]) == 1
        assert client.post("/redteam/chat", json={"prompt": "unknown", "conversation_id": str(uuid4())}).status_code == 404
        settings.user_oid = uuid4()
        # A different configured identity cannot load Alice's stored conversation.
        from redteam_bridge.conversations import ConversationStore
        with pytest.raises(BridgeError):
            ConversationStore(settings.conversations_path, settings.owner).load(one["conversation_id"])
    assert len(bodies) == 2


def test_approval_interrupt_is_reported_and_never_resumed(settings):
    calls = []
    interrupt = {"id": "int-create", "reason": "tool_call", "toolCallId": "create"}

    def upstream(request):
        calls.append(request)
        return sse([event("RUN_FINISHED", outcome={"type": "interrupt", "interrupts": [interrupt]})])

    with api(settings, upstream) as client:
        result = client.post("/redteam/chat", json={"prompt": "Create a ticket"}).json()
        assert result["status"] == "approval_required"
        assert result["interrupts"] == [interrupt]
        assert "approval" in result["response"]
        assert client.post("/redteam/chat", json={"prompt": "yes", "conversation_id": result["conversation_id"]}).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize("code,status", [
    ("gateway_rate_limited", 429), ("gateway_guardrail_blocked", 446),
    ("gateway_budget_exhausted", 412), ("agent_execution_failed", 502),
])
def test_stream_errors_are_not_reported_as_answers(settings, code, status):
    with api(settings, lambda request: sse([event("RUN_ERROR", code=code, message="Safe failure")])) as client:
        response = client.post("/redteam/chat", json={"prompt": "hello"})
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    if status == 429:
        assert response.headers["Retry-After"] == "60"


def test_partial_failed_turn_does_not_poison_history(settings):
    calls = []
    def upstream(request):
        calls.append(json.loads(request.content))
        if len(calls) == 2:
            return sse(answer()[:-1])
        return sse(answer())
    with api(settings, upstream) as client:
        first = client.post("/redteam/chat", json={"prompt": "first"}).json()
        cid = first["conversation_id"]
        failed = client.post("/redteam/chat", json={"prompt": "failed turn", "conversation_id": cid})
        assert failed.status_code == 502
        assert failed.json()["detail"]["code"] == "incomplete_agent_stream"
        assert client.post("/redteam/chat", json={"prompt": "retry", "conversation_id": cid}).status_code == 200
    assert "failed turn" not in json.dumps(calls[-1])


@pytest.mark.parametrize("upstream_status,expected", [(401, 503), (403, 403), (503, 503)])
def test_upstream_auth_and_service_failures(settings, upstream_status, expected):
    with api(settings, lambda request: httpx.Response(upstream_status, text="private details")) as client:
        response = client.post("/redteam/chat", json={"prompt": "hello"})
    assert response.status_code == expected
    assert "private details" not in response.text


def test_missing_user_authorization_never_calls_agent(settings):
    class Unauthorized:
        def get_token(self):
            raise BridgeError(503, "user_authorization_required", "Run login.")
    calls = []
    with api(settings, lambda request: calls.append(request) or sse(answer()), Unauthorized()) as client:
        response = client.post("/redteam/chat", json={"prompt": "hello"})
    assert response.status_code == 503
    assert not calls


def test_timeout_is_explicit(settings):
    def upstream(request):
        raise httpx.ReadTimeout("private network details")
    with api(settings, upstream) as client:
        response = client.post("/redteam/chat", json={"prompt": "hello"})
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "assistant_timeout"


def test_concurrent_turns_on_one_conversation_do_not_overwrite_history(settings):
    histories = []

    def upstream(request):
        histories.append(json.loads(request.content)["messages"])
        # Give another request an opportunity to enter the bridge while this turn
        # is in progress. Its loaded history must include this completed turn.
        time.sleep(0.02)
        events = answer()
        message_id = str(uuid4())
        for item in events:
            if "messageId" in item:
                item["messageId"] = message_id
        return sse(events)

    with api(settings, upstream) as client:
        cid = client.post("/redteam/chat", json={"prompt": "first"}).json()["conversation_id"]

        def follow_up(prompt):
            return client.post("/redteam/chat", json={"prompt": prompt, "conversation_id": cid})

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(follow_up, ["second", "third"]))
        assert all(response.status_code == 200 for response in responses)
    assert [len(history) for history in histories] == [1, 3, 5]
