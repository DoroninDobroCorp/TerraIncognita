"""GET /api/collections — category collections progress ("10/47 abandoned places")."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models.place_collections import CollectionsResponse
from app.services.place_collections import build_collections

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["collections"])


@router.get("/collections", response_model=CollectionsResponse)
async def get_collections(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(50.0, gt=0, le=50.0),  # clamped to TERRA_MAX_RADIUS_KM below
) -> CollectionsResponse:
    """Per category: places the explorer has visited vs places discoverable in the radius.

    Biggest collections first; categories with nothing nearby are omitted. Totals come from a
    single discovery page (the 200 best-ranked places), so in very dense areas they are a
    lower bound.
    """
    try:
        collections = await build_collections(lat, lng, radius_km)
    except Exception as exc:
        logger.exception("Collections error")
        raise HTTPException(status_code=500, detail="Collections lookup failed") from exc
    return CollectionsResponse(collections=collections)
