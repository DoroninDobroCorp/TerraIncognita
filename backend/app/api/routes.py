"""Smart Route Builder API endpoints (Epic 4).

POST /api/route         — point-to-point route with POI discovery (Story 4.1)
POST /api/route/explore — circular exploration route (Story 4.2)
POST /api/route/export  — export route to GPX/KML (Story 4.4)
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.models.route import (
    ExploreRouteRequest,
    RouteExportRequest,
    RouteExportResponse,
    RouteReorderRequest,
    RouteRequest,
    RouteResponse,
)
from app.services.route_builder import build_explore_route, build_route, reorder_waypoints
from app.services.route_export import export_gpx, export_kml

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["routes"])


@router.post("/route", response_model=RouteResponse)
async def create_route(req: RouteRequest) -> RouteResponse:
    """Build a route from origin to destination with POI discovery.

    Discovers interesting places in the corridor between origin and
    destination, optimizes the visit order (TSP + 2-opt), and fits
    the route within the time budget.
    """
    t0 = time.monotonic()
    try:
        result = await build_route(req)
    except Exception:
        logger.exception("Route building error")
        raise HTTPException(status_code=500, detail="Route building failed")
    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "POST /api/route → %d waypoints in %.0fms",
        len(result.route.waypoints), elapsed_ms,
    )
    return result


@router.post("/route/explore", response_model=RouteResponse)
async def create_explore_route(req: ExploreRouteRequest) -> RouteResponse:
    """Generate a circular exploration route from a starting point.

    Finds interesting places around the origin, builds an optimal
    circular route, and fits it within the time budget. Great for
    "I have 4 hours, show me the best nearby places" scenarios.
    """
    t0 = time.monotonic()
    try:
        result = await build_explore_route(req)
    except Exception:
        logger.exception("Explore route building error")
        raise HTTPException(status_code=500, detail="Explore route building failed")
    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "POST /api/route/explore → %d waypoints in %.0fms",
        len(result.route.waypoints), elapsed_ms,
    )
    return result


@router.post("/route/export", response_model=RouteExportResponse)
async def export_route(req: RouteExportRequest) -> RouteExportResponse:
    """Export a route to GPX or KML format.

    Supports GPX 1.1 (for GPS devices, OsmAnd, Maps.me) and
    KML (for Google Earth, Google Maps).
    """
    try:
        if req.format == "gpx":
            content = export_gpx(req.route, req.include_descriptions)
        elif req.format == "kml":
            content = export_kml(req.route, req.include_descriptions)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {req.format}")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Route export error")
        raise HTTPException(status_code=500, detail="Route export failed")

    timestamp = req.route.created_at.strftime("%Y%m%d_%H%M%S")
    filename = f"terra_incognita_route_{timestamp}.{req.format}"

    return RouteExportResponse(
        content=content,
        format=req.format,
        filename=filename,
    )


@router.post("/route/reorder", response_model=RouteResponse)
async def reorder_route(req: RouteReorderRequest) -> RouteResponse:
    """Reorder waypoints in an existing route (drag-and-drop).

    Accepts a route and a new order for POI waypoints (origin and
    destination stay fixed). Recalculates segments, distances, and
    durations for the new order.
    """
    try:
        result = reorder_waypoints(req.route, req.waypoint_order)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Route reorder error")
        raise HTTPException(status_code=500, detail="Route reorder failed")
    return result
