"""Application-owned data sent as AG-UI custom events."""

from __future__ import annotations

from pydantic import BaseModel


class Source(BaseModel):
    name: str
    url: str
