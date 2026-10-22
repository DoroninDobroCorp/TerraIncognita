"""Models for GET /api/streak."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StreakResponse(BaseModel):
    """Consecutive-days-with-activity counter, in the viewer's local calendar."""

    current: int = Field(0, ge=0, description="Length of the streak that is still alive")
    longest: int = Field(0, ge=0, description="Longest streak ever")
    last_active: str | None = Field(None, description="Most recent active local day, YYYY-MM-DD")
    active_today: bool = False
    days: list[str] = Field(
        default_factory=list,
        description="Active local days within the last 60 days, ascending, YYYY-MM-DD",
    )
