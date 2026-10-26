"""Read optional Portkey output checks before ending an OpenAI-compatible stream."""

import json
from collections.abc import AsyncIterator

import httpx


class PortkeyOutputBlocked(RuntimeError):
    """Portkey reported a failed output guardrail for this model response."""


async def adapt_portkey_stream(response: httpx.Response) -> None:
    """HTTPX response hook; leave non-streaming responses and HTTP errors alone."""
    if response.is_success and response.headers.get("content-type", "").startswith(
        "text/event-stream"
    ):
        source = httpx.Response(
            response.status_code, headers=response.headers, stream=response.stream
        )
        response.stream = _PortkeyStream(source)
        # The source decodes compression before we parse/re-encode SSE events.
        response.headers.pop("content-encoding", None)
        response.headers.pop("content-length", None)


class _PortkeyStream(httpx.AsyncByteStream):
    def __init__(self, source: httpx.Response) -> None:
        self._source = source

    async def __aiter__(self) -> AsyncIterator[bytes]:
        done = False
        async for lines in self._events():
            data = "\n".join(
                line[5:].removeprefix(" ")
                for line in lines
                if line.startswith("data:")
            )
            if data == "[DONE]":
                # The SDK stops here, but Portkey may append output checks later.
                done = True
                continue
            if data:
                payload = json.loads(data)
                hooks = payload.get("hook_results") or {}
                if any(
                    hook.get("verdict") is False
                    for hook in hooks.get("after_request_hooks") or []
                ):
                    raise PortkeyOutputBlocked(
                        "The streamed response failed output guardrails."
                    )
                # Hook-only events are not ChatCompletionChunks. A combined event
                # can still carry normal content, usage, or an upstream error.
                if "hook_results" in payload and not any(
                    payload.get(key) for key in ("choices", "usage", "error")
                ):
                    continue
            yield ("\n".join(lines) + "\n\n").encode("utf-8")

        # EOF, not a verdict, is the completion condition. No checks is valid.
        if not done:
            raise RuntimeError("Portkey stream ended without [DONE].")
        yield b"data: [DONE]\n\n"

    async def _events(self) -> AsyncIterator[list[str]]:
        # HTTPX handles UTF-8 and CRLF across arbitrary network byte boundaries.
        lines: list[str] = []
        async for line in self._source.aiter_lines():
            if line:
                lines.append(line)
            elif lines:
                yield lines
                lines = []
        if lines:
            yield lines

    async def aclose(self) -> None:
        await self._source.aclose()
