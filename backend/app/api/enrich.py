"""POST /api/place/enrich — Wikipedia summary + Commons photos for a place."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.models.place import EnrichRequest, EnrichResponse
from app.services.enrichment import enrich_place

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["enrichment"])


@router.post("/place/enrich", response_model=EnrichResponse)
async def enrich(req: EnrichRequest) -> EnrichResponse:
    """Enrich a place with its Wikipedia summary and freely-licensed Commons photos.

    Wikipedia/Commons outages degrade the answer (empty fields) instead of failing the request.
    """
    try:
        return await enrich_place(req)
    except Exception:
        logger.exception("Enrichment failed unexpectedly")
        return EnrichResponse()
