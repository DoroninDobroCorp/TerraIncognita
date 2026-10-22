"""POST /api/route/loop — round trip from the start point within a time budget."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.models.loop_route import LoopRouteRequest
from app.models.route import RouteResponse
from app.services.loop_route import LoopParamsError, NotEnoughPlacesError, build_loop_route

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["routes"])


@router.post("/route/loop", response_model=RouteResponse)
async def create_loop_route(req: LoopRouteRequest) -> RouteResponse:
    """Build a closed tour (start == end) through 2..max_stops places that fits the time.

    Stops are chosen by quality with geographic spread around the start, ordered to a short
    tour, and trimmed until travel plus visiting time fits ``duration_hours`` (minus a small
    safety margin). The response is a regular route: export it with /api/route/export.
    """
    try:
        return await build_loop_route(req)
    except LoopParamsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotEnoughPlacesError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Loop route error")
        raise HTTPException(status_code=500, detail="Loop route building failed") from exc
