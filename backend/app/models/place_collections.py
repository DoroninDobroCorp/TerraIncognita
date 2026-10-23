"""Models for GET /api/collections."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Collection(BaseModel):
    """Progress in one category: places visited out of those discoverable nearby."""

    category: str
    label: str
    icon: str
    visited: int = Field(0, ge=0)
    total: int = Field(0, ge=0)


class CollectionsResponse(BaseModel):
    collections: list[Collection] = Field(default_factory=list)
