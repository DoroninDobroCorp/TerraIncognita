"""GET /api/streak — consecutive days with exploring activity."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models.streak import StreakResponse
from app.services.streak import MAX_TZ_OFFSET_MINUTES, get_streak

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["streak"])


@router.get("/streak", response_model=StreakResponse)
async def read_streak(
    tz_offset_minutes: int = Query(
        0,
        ge=-MAX_TZ_OFFSET_MINUTES,
        le=MAX_TZ_OFFSET_MINUTES,
        description="Viewer's offset from UTC in minutes, east positive (UTC+3 = 180)",
    ),
) -> StreakResponse:
    """Streak of consecutive local days with at least one visit.

    Alive while the last active day is today or yesterday. ``days`` lists the active days of
    the last 60 (oldest first) for a calendar strip.
    """
    try:
        result = await get_streak(tz_offset_minutes)
    except Exception as exc:
        logger.exception("Streak computation error")
        raise HTTPException(status_code=500, detail="Streak computation failed") from exc
    return StreakResponse(
        current=result.current,
        longest=result.longest,
        last_active=result.last_active.isoformat() if result.last_active else None,
        active_today=result.active_today,
        days=[d.isoformat() for d in result.days],
    )
