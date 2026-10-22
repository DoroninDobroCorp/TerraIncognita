"""POST /api/story/walk — a short essay about a finished walk."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.models.walk_report import WalkReportRequest, WalkReportResponse
from app.services.walk_report import generate_walk_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["storytelling"])


@router.post("/story/walk", response_model=WalkReportResponse)
async def walk_report(req: WalkReportRequest) -> WalkReportResponse:
    """Turn the places visited on a walk and the distance covered into a short journal entry.

    Cached per walk and language. When the AI is unavailable a plain summary is returned with
    ``ai_generated: false`` instead of an error.
    """
    try:
        report, ai_generated = await generate_walk_report(req)
    except Exception as exc:
        logger.exception("Walk report error")
        raise HTTPException(status_code=500, detail="Walk report failed") from exc
    return WalkReportResponse(report=report, ai_generated=ai_generated)
