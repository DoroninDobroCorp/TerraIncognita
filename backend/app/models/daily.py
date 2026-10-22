"""Models for GET /api/daily."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.place import Place


class DailyPlaceResponse(BaseModel):
    """Today's pick for a neighbourhood."""

    date: str = Field(..., description="UTC date the pick is valid for, YYYY-MM-DD")
    place: Place
    reason: str = Field(..., description="Short, deterministic sentence on why it was picked")
    story: str | None = Field(
        None, description="Short LLM story in the requested language; null when unavailable"
    )
