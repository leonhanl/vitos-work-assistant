import asyncio
import gzip
import json

import httpx
import pytest

from work_assistant.portkey_stream import PortkeyOutputBlocked, adapt_portkey_stream


class GatewayStream(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("before_done", [False, True])
def test_failed_output_hook_is_detected_on_either_side_of_done(before_done):
    hook = b'data: {"hook_results":{"after_request_hooks":[{"verdict":true},{"verdict":false}]}}\n\n'
    done = b"data: [DONE]\n\n"
    body = GatewayStream([hook, done] if before_done else [done, hook])

    async def run_test():
        response = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
        try:
            await adapt_portkey_stream(response)
            with pytest.raises(PortkeyOutputBlocked):
                await response.aread()
        finally:
            await response.aclose()
        assert body.closed

    asyncio.run(run_test())


@pytest.mark.parametrize("error", [httpx.ReadTimeout("tail timed out"), httpx.RemoteProtocolError("connection lost")])
def test_network_failure_after_done_is_not_success(error):
    async def run_test():
        response = httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            stream=GatewayStream([b"data: [DONE]\n\n"], error),
        )
        try:
            await adapt_portkey_stream(response)
            with pytest.raises(type(error)):
                await response.aread()
        finally:
            await response.aclose()

    asyncio.run(run_test())


def test_eof_without_done_is_not_success():
    async def run_test():
        response = httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            stream=GatewayStream([b'data: {"choices":[]}\n\n']),
        )
        await adapt_portkey_stream(response)
        with pytest.raises(RuntimeError, match="without \\[DONE\\]"):
            await response.aread()

    asyncio.run(run_test())


@pytest.mark.parametrize("status,content_type", [(446, "text/event-stream"), (200, "application/json")])
def test_http_errors_and_non_streaming_responses_are_unchanged(status, content_type):
    body = GatewayStream([b'{"error":{"message":"original response"}}'])

    async def run_test():
        response = httpx.Response(status, headers={"content-type": content_type}, stream=body)
        await adapt_portkey_stream(response)
        assert response.stream is body
        assert await response.aread() == b'{"error":{"message":"original response"}}'

    asyncio.run(run_test())


def test_input_hooks_and_combined_content_still_stream():
    payload = {
        "choices": [{"delta": {"content": "answer"}}],
        "hook_results": {"before_request_hooks": [{"verdict": False}]},
    }

    async def run_test():
        response = httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            stream=GatewayStream([f"data: {json.dumps(payload)}\n\n".encode(), b"data: [DONE]\n\n"]),
        )
        await adapt_portkey_stream(response)
        assert b'"content": "answer"' in await response.aread()

    asyncio.run(run_test())


@pytest.mark.parametrize("verdict", [None, True, False])
def test_compressed_stream_with_optional_hooks(verdict):
    content = b'data: {"choices":[{"delta":{"content":"answer"}}],"hook_results":null}\n\n'
    content += b"data: [DONE]\n\n"
    if verdict is not None:
        content += f'data: {json.dumps({"hook_results": {"after_request_hooks": [{"verdict": verdict}]}})}\n\n'.encode()

    async def run_test():
        response = httpx.Response(
            200, headers={"content-type": "text/event-stream", "content-encoding": "gzip"},
            stream=GatewayStream([gzip.compress(content)]),
        )
        try:
            await adapt_portkey_stream(response)
            if verdict is False:
                with pytest.raises(PortkeyOutputBlocked):
                    await response.aread()
            else:
                result = await response.aread()
                assert b'"content":"answer"' in result
                assert result.endswith(b"data: [DONE]\n\n")
        finally:
            await response.aclose()

    asyncio.run(run_test())
