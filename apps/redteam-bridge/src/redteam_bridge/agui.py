"""Reduce the AG-UI events emitted by Work Assistant into client-owned history."""

import json
from collections.abc import Iterator

from redteam_bridge.errors import BridgeError


def read_events(lines: Iterator[str]) -> Iterator[dict]:
    data: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif not line and data:
            yield json.loads("\n".join(data))
            data = []
    if data:
        yield json.loads("\n".join(data))


class Run:
    def __init__(self, conversation: dict) -> None:
        self.messages = conversation["messages"]
        self.state = conversation["state"]
        self.finished = False
        self.interrupts: list[dict] = []
        self.text_ids: list[str] = []
        self.trace_id: str | None = None
        self.sources: list[dict] = []

    def _message(self, message_id: str) -> dict:
        return next(message for message in self.messages if message["id"] == message_id)

    def _tool(self, tool_call_id: str) -> dict:
        return next(
            tool for message in self.messages
            for tool in message.get("toolCalls", []) if tool["id"] == tool_call_id
        )

    def apply(self, event: dict) -> None:
        kind = event["type"]
        if kind in {"TEXT_MESSAGE_START", "REASONING_START", "REASONING_MESSAGE_START"}:
            message_id = event["messageId"]
            if not any(message["id"] == message_id for message in self.messages):
                self.messages.append({
                    "id": message_id,
                    "role": "assistant" if kind == "TEXT_MESSAGE_START" else "reasoning",
                    "content": "",
                })
            if kind == "TEXT_MESSAGE_START":
                self.text_ids.append(message_id)
        elif kind in {"TEXT_MESSAGE_CONTENT", "REASONING_MESSAGE_CONTENT"}:
            self._message(event["messageId"])["content"] += event["delta"]
        elif kind == "TOOL_CALL_START":
            parent = event.get("parentMessageId") or event["toolCallId"]
            if not any(message["id"] == parent for message in self.messages):
                self.messages.append({"id": parent, "role": "assistant"})
            self._message(parent).setdefault("toolCalls", []).append({
                "id": event["toolCallId"], "type": "function",
                "function": {"name": event["toolCallName"], "arguments": ""},
            })
        elif kind == "TOOL_CALL_ARGS":
            self._tool(event["toolCallId"])["function"]["arguments"] += event["delta"]
        elif kind == "TOOL_CALL_RESULT":
            # Keep parallel tool results immediately after their owning assistant
            # message, before any later assistant response.
            index = next(
                i for i, message in enumerate(self.messages)
                if any(tool["id"] == event["toolCallId"] for tool in message.get("toolCalls", []))
            ) + 1
            while index < len(self.messages) and self.messages[index]["role"] == "tool":
                index += 1
            self.messages.insert(index, {
                "id": event["messageId"], "role": "tool",
                "toolCallId": event["toolCallId"], "content": event["content"],
            })
        elif kind == "REASONING_ENCRYPTED_VALUE":
            entity = (
                self._tool(event["entityId"])
                if event["subtype"] == "tool-call" else self._message(event["entityId"])
            )
            # This also carries Pydantic AI tool-kind and tool-outcome metadata;
            # preserving it matters for load_capability and failed tool calls.
            entity["encryptedValue"] = event["encryptedValue"]
        elif kind == "MESSAGES_SNAPSHOT":
            self.messages = event["messages"]
        elif kind == "STATE_SNAPSHOT":
            self.state = event["snapshot"]
        elif kind == "ACTIVITY_SNAPSHOT":
            message = {
                "id": event["messageId"], "role": "activity",
                "activityType": event["activityType"], "content": event["content"],
            }
            old = next((m for m in self.messages if m["id"] == message["id"]), None)
            if old is None:
                self.messages.append(message)
            elif event.get("replace", True):
                old.update(message)
        elif kind == "CUSTOM":
            if event["name"] == "trace":
                self.trace_id = event["value"]["trace_id"]
            elif event["name"] == "sources":
                self.sources = event["value"]
        elif kind == "RUN_FINISHED":
            self.finished = True
            outcome = event.get("outcome") or {}
            if outcome.get("type") == "interrupt":
                self.interrupts = outcome["interrupts"]
                if not self.interrupts:
                    raise BridgeError(502, "invalid_agent_stream", "Empty approval interrupt.")
        elif kind == "RUN_ERROR":
            code = event.get("code", "agent_execution_failed")
            status = {
                "gateway_rate_limited": 429,
                "gateway_guardrail_blocked": 446,
                "gateway_budget_exhausted": 412,
            }.get(code, 502)
            raise BridgeError(status, code, event.get("message", "The assistant run failed."))
        elif kind not in {
            "RUN_STARTED", "STEP_STARTED", "STEP_FINISHED", "TEXT_MESSAGE_END",
            "TOOL_CALL_END", "REASONING_MESSAGE_END", "REASONING_END", "RAW",
        }:
            # No JSON-patch state events are emitted by the current Agent. Fail
            # explicitly if the upstream protocol changes instead of losing state.
            raise BridgeError(502, "unsupported_agent_event", f"Unsupported AG-UI event: {kind}.")

    def result(self, conversation_id: str) -> dict:
        if not self.finished:
            raise BridgeError(502, "incomplete_agent_stream", "The assistant stream ended before RUN_FINISHED.")
        response = next(
            (self._message(mid).get("content", "") for mid in reversed(self.text_ids)
             if self._message(mid).get("content")),
            "",
        )
        if not response and self.interrupts:
            response = "Human approval is required before the assistant can continue."
        if not response:
            raise BridgeError(502, "empty_agent_response", "The assistant returned no answer.")
        return {
            "response": response,
            "conversation_id": conversation_id,
            "status": "approval_required" if self.interrupts else "completed",
            "interrupts": self.interrupts,
            "trace_id": self.trace_id,
            "sources": self.sources,
        }

    def conversation(self) -> dict:
        return {"messages": self.messages, "state": self.state, "interrupts": self.interrupts}
