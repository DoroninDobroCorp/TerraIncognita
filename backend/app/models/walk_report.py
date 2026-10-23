"""Models for POST /api/story/walk."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_WALK_PLACES = 20


class WalkPlace(BaseModel):
    """One place visited on the walk, in the order it was visited."""

    name: str | None = None
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    categories: list[str] = Field(default_factory=list)
    description: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _cut_name(cls, v: Any) -> Any:
        return v[:120] if isinstance(v, str) else v

    @field_validator("description", mode="before")
    @classmethod
    def _cut_description(cls, v: Any) -> Any:
        return v[:300] if isinstance(v, str) else v

    @field_validator("categories", mode="before")
    @classmethod
    def _cut_categories(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        return [c[:40] for c in v if isinstance(c, str)][:5]


class WalkReportRequest(BaseModel):
    places: list[WalkPlace] = Field(..., min_length=1, max_length=MAX_WALK_PLACES)
    total_distance_km: float = Field(..., ge=0, le=1000)
    duration_minutes: float | None = Field(None, ge=0, le=24 * 60)
    language: str = Field("auto", pattern=r"^(auto|[a-z]{2})$")


class WalkReportResponse(BaseModel):
    report: str
    ai_generated: bool = Field(
        ..., description="False when the AI was unavailable and a plain summary was returned"
    )
