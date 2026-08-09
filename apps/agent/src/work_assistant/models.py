"""Public API request and response models."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    thread_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value

    @field_validator("thread_id")
    @classmethod
    def thread_id_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("thread_id must not be blank")
        return value


class Source(BaseModel):
    name: str
    url: str


class ChatResponse(BaseModel):
    thread_id: str
    answer: str
    sources: list[Source] = Field(default_factory=list)

