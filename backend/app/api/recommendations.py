"""Story 2.3 — Contextual Recommendations API endpoint.

POST /api/recommend — personalized place recommendations.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.models.chat import RecommendationRequest, RecommendationResponse
from app.services.llm_recommendations import get_recommendations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["recommendations"])


@router.post("/recommend", response_model=RecommendationResponse)
async def recommend_places(req: RecommendationRequest) -> RecommendationResponse:
    """Get personalized place recommendations.

    Takes user preferences (favorite categories, liked/disliked places),
    current context (time, weather), and returns curated recommendations
    with reasons explaining why each place is a good match.

    Strategies:
    - cold_start: new user, rule-based ranking
    - personalized: uses LLM with preference data
    - diverse: mixes categories for variety
    """
    t0 = time.monotonic()
    try:
        result = await get_recommendations(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Recommendation engine error")
        raise HTTPException(status_code=500, detail="Recommendation engine failed")

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "POST /api/recommend → %d recs, strategy=%s in %.0fms",
        result.total, result.strategy, elapsed_ms,
    )
    return result
