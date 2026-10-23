"""Story 2.2 — Description Generation API endpoint.

POST /api/describe — generate atmospheric AI descriptions for places.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.models.chat import DescriptionRequest, DescriptionResponse
from app.services.llm_descriptions import generate_description
from app.utils.safe_route import SafeValidationRoute

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["descriptions"], route_class=SafeValidationRoute)


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
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as exc:
        logger.exception("Description generation error for %r", req.place_id)
        raise HTTPException(status_code=500, detail="Description generation failed") from exc

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "POST /api/describe → place=%r, cached=%s in %.0fms",
        req.place_id, result.cached, elapsed_ms,
    )
    return result
