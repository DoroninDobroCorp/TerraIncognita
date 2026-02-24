"""Story 2.2 — Description Generation API endpoint.

POST /api/describe — generate atmospheric AI descriptions for places.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.models.chat import DescriptionRequest, DescriptionResponse
from app.services.llm_descriptions import generate_description

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["descriptions"])


@router.post("/describe", response_model=DescriptionResponse)
async def describe_place(req: DescriptionRequest) -> DescriptionResponse:
    """Generate an atmospheric AI description for a place.

    Uses Claude to create engaging, practical descriptions based on
    available metadata. Results are cached for 7 days.
    Descriptions are marked as AI-generated.
    """
    t0 = time.monotonic()
    try:
        result = await generate_description(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Description generation error for %s", req.place_id)
        raise HTTPException(status_code=500, detail="Description generation failed")

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "POST /api/describe → place=%s, cached=%s in %.0fms",
        req.place_id, result.cached, elapsed_ms,
    )
    return result
