"""Application-owned data sent as AG-UI custom events."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Source(BaseModel):
    name: str
    url: str


class FeedbackRequest(BaseModel):
    """A user's rating for one completed Agent run."""

    model_config = ConfigDict(extra="forbid")

    trace_id: UUID
    value: Annotated[int, Field(strict=True, ge=1, le=5)]
