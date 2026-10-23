"""Models for POST /api/route/loop (the response is the regular RouteResponse)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.place import PlaceCategory


class LoopRouteRequest(BaseModel):
    """Round trip from (lat, lng) back to (lat, lng) within a time budget."""

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    duration_hours: float = Field(
        3.0, ge=0.5, le=12.0, description="Total time available: travel plus visiting"
    )
    mode: Literal["walking", "cycling"] = "walking"
    categories: list[PlaceCategory] = Field(
        default_factory=list, description="Only consider places in these categories"
    )
    max_stops: int = Field(5, ge=2, le=8, description="Upper bound on the number of stops")
    exclude_visited: list[str] = Field(
        default_factory=list, max_length=1000, description="Place ids to leave out"
    )
