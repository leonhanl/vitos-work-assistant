"""API-key protected REST facade; the original Agent still validates Token A."""

import logging
from contextlib import asynccontextmanager
from secrets import compare_digest
from threading import Lock
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from redteam_bridge.agui import Run, read_events
from redteam_bridge.auth import UserTokenCache
from redteam_bridge.config import Settings
from redteam_bridge.conversations import ConversationStore
from redteam_bridge.errors import BridgeError

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    conversation_id: UUID | None = None
    guardrails_enabled: bool = Field(default=False, strict=True)

    @field_validator("prompt")
    @classmethod
    def require_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class Bridge:
    def __init__(
        self, settings: Settings, tokens: UserTokenCache, client: httpx.Client
    ) -> None:
        self.settings = settings
        self.tokens = tokens
        self.client = client
        self.store = ConversationStore(settings.conversations_path, settings.owner)
        self._locks: WeakValueDictionary[str, Lock] = WeakValueDictionary()
        self._locks_guard = Lock()

    def chat(self, chat: ChatRequest) -> dict:
        conversation_id = str(chat.conversation_id or uuid4())
        with self._locks_guard:
            lock = self._locks.setdefault(conversation_id, Lock())
        # Different conversations can run concurrently; one conversation's turns
        # must be serialized to avoid overwriting its tool history.
        with lock:
            conversation = (
                self.store.load(conversation_id) if chat.conversation_id
                else {"messages": [], "state": {}, "interrupts": []}
            )
            if conversation["interrupts"]:
                raise BridgeError(
                    409, "approval_required",
                    "This conversation is waiting for human approval. "
                    "The bridge does not approve actions; start a new conversation.",
                )
            token = self.tokens.get_token()
            conversation["messages"].append({
                "id": str(uuid4()), "role": "user", "content": chat.prompt,
            })
            run = Run(conversation)
            try:
                with self.client.stream(
                    "POST", str(self.settings.assistant_url),
                    headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
                    json={
                        "threadId": conversation_id, "runId": str(uuid4()),
                        "state": run.state, "messages": run.messages,
                        "tools": [], "context": [],
                        "forwardedProps": {"guardrails_enabled": chat.guardrails_enabled},
                    },
                ) as response:
                    if response.status_code == 401:
                        raise BridgeError(
                            503, "user_authorization_required",
                            "The Agent rejected Token A. Reauthorize the test user "
                            "and check Entra configuration.",
                        )
                    if response.status_code >= 400:
                        raise BridgeError(
                            response.status_code, "assistant_request_failed",
                            "Work Assistant rejected the request. "
                            "Check the Agent logs and user authorization.",
                        )
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        raise BridgeError(502, "invalid_agent_stream", "Expected an AG-UI event stream.")
                    for event in read_events(response.iter_lines()):
                        run.apply(event)
                result = run.result(conversation_id)
            except httpx.TimeoutException:
                raise BridgeError(504, "assistant_timeout", "Work Assistant timed out.") from None
            except httpx.HTTPError:
                raise BridgeError(502, "assistant_unavailable", "Work Assistant could not be reached.") from None
            except (ValueError, KeyError, StopIteration, TypeError):
                raise BridgeError(
                    502, "invalid_agent_stream",
                    "Work Assistant returned malformed AG-UI events.",
                ) from None
            # Failed / partial runs never poison the next turn's persisted history.
            self.store.save(conversation_id, run.conversation())
            return result


def create_app(
    settings: Settings | None = None,
    tokens: UserTokenCache | None = None,
    client: httpx.Client | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configured = settings or Settings()
        http = client or httpx.Client(timeout=configured.request_timeout)
        app.state.settings = configured
        app.state.bridge = Bridge(configured, tokens or UserTokenCache(configured), http)
        try:
            yield
        finally:
            if client is None:
                http.close()

    app = FastAPI(title="Work Assistant Red Team Bridge", lifespan=lifespan)

    @app.exception_handler(BridgeError)
    async def bridge_error(request: Request, error: BridgeError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else {}
        if error.status == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(
            status_code=error.status,
            content={"detail": {"code": error.code, "message": error.message}},
            headers=headers,
        )

    def authenticate(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        expected = request.app.state.settings.api_key.get_secret_value()
        if credentials is None or not compare_digest(
            credentials.credentials.encode(), expected.encode()
        ):
            raise BridgeError(401, "invalid_api_key", "A valid bridge API key is required.")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/redteam/chat", dependencies=[Depends(authenticate)])
    def chat(body: ChatRequest, request: Request) -> dict:
        try:
            return request.app.state.bridge.chat(body)
        except BridgeError:
            raise
        except Exception as error:
            # Exception messages can contain HTTP headers or token-cache contents.
            logger.error("Bridge request failed type=%s", type(error).__name__)
            raise BridgeError(
                500, "bridge_error",
                "The bridge failed. Check its configuration and server logs.",
            ) from None

    return app


app = create_app()
