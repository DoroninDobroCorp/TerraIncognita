"""Story 1.6 — Search API.

POST /api/discover — unified discovery endpoint with filtering,
sorting and cursor-based pagination.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.models.place import DiscoverRequest, DiscoverResponse
from app.services.discovery import discover

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["discovery"])


@router.post("/discover", response_model=DiscoverResponse)
async def discover_places(req: DiscoverRequest) -> DiscoverResponse:
    """Discover interesting and unusual places around a location.

    Aggregates data from OSM, Atlas Obscura, Wikidata,
    applies deduplication, classification and scoring.
    Target response time: <3s (with cache <500ms).
    """
    t0 = time.monotonic()
    try:
        result = await discover(req)
    except Exception:
        logger.exception("Discovery pipeline error")
        raise HTTPException(status_code=500, detail="Discovery pipeline failed")
    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info("POST /api/discover → %d places in %.0fms", result.total, elapsed_ms)
    return result
