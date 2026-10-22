"""GET /api/daily — the place of the day."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models.daily import DailyPlaceResponse
from app.services.daily_place import NoPlacesNearbyError, get_daily_place

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["daily"])


@router.get("/daily", response_model=DailyPlaceResponse)
async def daily_place(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(10.0, gt=0, le=50.0),
    lang: str = Query("en", pattern=r"^(auto|[a-z]{2})$", description="Language of the story"),
) -> DailyPlaceResponse:
    """One unusual, well-documented place for today, the same for the whole neighbourhood.

    The radius widens automatically (10 -> 25 -> 50 km) when nothing is nearby. ``story`` is
    a short AI-written blurb cached per place and language, or null when AI is unavailable.
    """
    try:
        return await get_daily_place(lat, lng, radius_km, lang)
    except NoPlacesNearbyError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No places found within {exc.searched_km:g} km of here. Try a different "
                "location (towns and trails have more to discover), or try again in a few "
                "minutes in case a data source was unavailable."
            ),
        ) from exc
    except Exception as exc:
        logger.exception("Daily place error")
        raise HTTPException(status_code=500, detail="Daily place lookup failed") from exc
