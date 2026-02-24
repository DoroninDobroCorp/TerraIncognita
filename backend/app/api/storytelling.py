"""Story 2.4 — Storytelling API endpoint (text generation, TTS deferred to V2).

POST /api/story — generate immersive narrative for a single place
POST /api/story/route — generate connected narrative for a route
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.models.chat import (
    RouteStoryRequest,
    RouteStoryResponse,
    StoryRequest,
    StoryResponse,
)
from app.models.place import Coordinates, Place, PlaceSource
from app.services.llm_storytelling import generate_place_story, generate_route_story

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["storytelling"])


@router.post("/story", response_model=StoryResponse)
async def get_place_story(req: StoryRequest) -> StoryResponse:
    """Generate an immersive narrative for a place.

    Mixes facts, legends, and atmospheric description.
    Stories are cached for 7 days. Text only — TTS is deferred to V2.
    """
    t0 = time.monotonic()

    place = Place(
        id=req.place_id,
        source=PlaceSource.OSM,
        name=req.place_name,
        coordinates=Coordinates(lat=req.lat, lng=req.lng),
        categories=req.place_categories,
        tags=req.place_tags,
        metadata=req.place_metadata,
    )

    try:
        result = await generate_place_story(place, language=req.language)
    except Exception:
        logger.exception("Story generation error for %s", req.place_id)
        raise HTTPException(status_code=500, detail="Story generation failed")

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "POST /api/story → place=%s, ai=%s in %.0fms",
        req.place_id, result["ai_generated"], elapsed_ms,
    )

    return StoryResponse(
        story=result["story"],
        place_id=result["place_id"],
        place_name=result.get("place_name"),
        ai_generated=result["ai_generated"],
        language=result["language"],
    )


@router.post("/story/route", response_model=RouteStoryResponse)
async def get_route_story(req: RouteStoryRequest) -> RouteStoryResponse:
    """Generate a connected narrative for a multi-stop route.

    Creates transitions between stops, builds anticipation,
    and weaves the journey into a coherent adventure story.
    """
    t0 = time.monotonic()

    places = [
        Place(
            id=p.place_id,
            source=PlaceSource.OSM,
            name=p.place_name,
            coordinates=Coordinates(lat=p.lat, lng=p.lng),
            categories=p.place_categories,
            tags=p.place_tags,
            metadata=p.place_metadata,
        )
        for p in req.places
    ]

    try:
        result = await generate_route_story(places, language=req.language)
    except Exception:
        logger.exception("Route story generation error")
        raise HTTPException(status_code=500, detail="Route story generation failed")

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "POST /api/story/route → %d stops, ai=%s in %.0fms",
        len(places), result["ai_generated"], elapsed_ms,
    )

    return RouteStoryResponse(
        story=result["story"],
        place_ids=result["place_ids"],
        ai_generated=result["ai_generated"],
        language=result["language"],
    )
