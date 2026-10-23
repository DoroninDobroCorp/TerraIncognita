"""Offline mode API endpoints (Epic 7): tiles, place cache, sync, navigation."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.models.offline import (
    CachePlacesRequest,
    CachePlacesResponse,
    OfflineNavRequest,
    OfflineNavResponse,
    OfflineStatus,
    RegionListResponse,
    SyncQueueResponse,
    SyncRequest,
    SyncResult,
    TileDownloadRequest,
    TileDownloadResponse,
)
from app.models.place import Coordinates, DiscoverRequest
from app.services.discovery import discover
from app.services.offline_manager import RouteNotFoundError, StorageLimitError, offline_manager
from app.utils.safe_route import SafeValidationRoute

logger = logging.getLogger(__name__)

router = APIRouter(tags=["offline"], route_class=SafeValidationRoute)


# ------------------------------------------------------------------
# Tile region management
# ------------------------------------------------------------------


@router.post("/api/offline/tiles/download", response_model=TileDownloadResponse)
async def download_tiles(req: TileDownloadRequest):
    """Initiate tile download for a geographic region."""
    if req.bbox.min_lat >= req.bbox.max_lat or req.bbox.min_lng >= req.bbox.max_lng:
        raise HTTPException(400, "Invalid bounding box: min must be less than max")
    if req.min_zoom > req.max_zoom:
        raise HTTPException(400, "min_zoom must be ≤ max_zoom")

    try:
        region, tile_count, est_mb = await offline_manager.create_region(
            name=req.name,
            bbox=req.bbox,
            tile_source=req.tile_source,
            min_zoom=req.min_zoom,
            max_zoom=req.max_zoom,
        )
    except StorageLimitError as exc:
        raise HTTPException(507, str(exc)) from exc
    except ValueError as exc:  # e.g. RegionTooLargeError
        raise HTTPException(400, str(exc)) from exc
    return TileDownloadResponse(
        region=region,
        estimated_tile_count=tile_count,
        estimated_size_mb=est_mb,
    )


@router.get("/api/offline/tiles/regions", response_model=RegionListResponse)
async def list_regions():
    """List all downloaded offline regions."""
    regions = await offline_manager.list_regions()
    total = sum(r.disk_usage_bytes for r in regions)
    return RegionListResponse(regions=regions, total_disk_usage_bytes=total)


@router.get("/api/offline/tiles/regions/{region_id}")
async def get_region(region_id: str):
    """Get details of a specific offline region."""
    try:
        region = await offline_manager.get_region(region_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid region ID") from exc
    if not region:
        raise HTTPException(404, "Region not found")
    return region


@router.delete("/api/offline/tiles/regions/{region_id}")
async def delete_region(region_id: str):
    """Delete a downloaded region and free storage."""
    try:
        deleted = await offline_manager.delete_region(region_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid region ID") from exc
    if not deleted:
        raise HTTPException(404, "Region not found")
    return {"status": "deleted", "region_id": region_id}


# ------------------------------------------------------------------
# Place cache
# ------------------------------------------------------------------


@router.post("/api/offline/places/cache", response_model=CachePlacesResponse)
async def cache_places(req: CachePlacesRequest):
    """Cache discovered places for a bounding box for offline access."""
    # Route-based caching: cache all places from a planned route
    if req.route_id:
        try:
            cached_count = await offline_manager.cache_route_places(req.route_id)
        except StorageLimitError as exc:
            raise HTTPException(507, str(exc)) from exc
        except RouteNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        total_cached, total_size = await offline_manager.cache_stats()
        return CachePlacesResponse(
            cached_count=cached_count, total_cached=total_cached, disk_usage_bytes=total_size
        )

    # BBox-based caching: discover places and cache them
    from app.services.offline_manager import _haversine

    bbox = req.bbox
    center_lat = (bbox.min_lat + bbox.max_lat) / 2
    center_lng = (bbox.min_lng + bbox.max_lng) / 2
    radius_m = _haversine(bbox.min_lat, bbox.min_lng, bbox.max_lat, bbox.max_lng) / 2
    radius_km = max(min(radius_m / 1000, settings.max_radius_km), 0.1)  # DiscoverRequest: 0 < r <= max

    source_status: dict[str, str] = {}
    try:
        result = await discover(DiscoverRequest(
            lat=center_lat,
            lng=center_lng,
            radius_km=radius_km,
            categories=list(req.categories),
            limit=min(200, settings.max_limit),
            sort_by="confidence",
        ))
        source_status = result.source_status
        # The search circle also covers the corners outside the rectangle the user asked for.
        places = [
            p for p in result.places
            if bbox.min_lat <= p.coordinates.lat <= bbox.max_lat
            and bbox.min_lng <= p.coordinates.lng <= bbox.max_lng
        ]
    except Exception:
        # Graceful degradation: if offline, return what's already cached
        logger.warning("Discovery failed (possibly offline), returning existing cache", exc_info=True)
        places = []

    # A place's id depends on which sources answered. When a better-ranked source joins a place we
    # cached earlier under another id, that older entry is the same place: replace it.
    # Ids of Deep Research and Atlas entries derive from the name alone ("Old Mill" in two towns share one), so an
    # entry is replaced only when it lies where the merged place does.
    same_place_m = max(settings.dedup_distance_meters, settings.dedup_name_match_distance_meters)
    for place in places:
        for old_id in place.metadata.get("merged_ids", ()):
            try:
                old = await offline_manager.get_cached_place(old_id)
                if old is not None and _haversine(
                    old.place.coordinates.lat, old.place.coordinates.lng, place.coordinates.lat, place.coordinates.lng
                ) <= same_place_m:
                    await offline_manager.delete_cached_place(old_id)
            except ValueError:
                continue  # not a safe id: it could never have been cached

    try:
        # No LLM description is generated here (budget), so never flag one as present.
        cached_count = await offline_manager.cache_places(places, has_llm_description=False)
    except StorageLimitError as exc:
        raise HTTPException(507, str(exc)) from exc

    total_cached, total_size = await offline_manager.cache_stats()

    degraded = sorted(
        name for name, status in source_status.items()
        if status in ("timeout", "partial") or (status == "error" and name != "atlas")
    )
    warning = None
    if not places:
        warning = "Nothing was found for this area — the data sources may be unreachable. Try again in a while."
    elif degraded:
        warning = f"{', '.join(degraded)} did not answer in full: what was cached may be incomplete."
    return CachePlacesResponse(
        cached_count=cached_count,
        total_cached=total_cached,
        disk_usage_bytes=total_size,
        source_status=source_status,
        warning=warning,
    )


@router.get("/api/offline/places/{place_id}")
async def get_cached_place(place_id: str):
    """Retrieve a cached place by ID."""
    try:
        cp = await offline_manager.get_cached_place(place_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid place ID") from exc
    if not cp:
        raise HTTPException(404, "Place not found in offline cache")
    return cp


@router.get("/api/offline/places")
async def list_cached_places(
    min_lat: float | None = Query(None, ge=-90, le=90),
    min_lng: float | None = Query(None, ge=-180, le=180),
    max_lat: float | None = Query(None, ge=-90, le=90),
    max_lng: float | None = Query(None, ge=-180, le=180),
):
    """List all cached places, optionally filtered by bounding box."""
    from app.models.offline import BoundingBox

    bbox = None
    if all(v is not None for v in (min_lat, min_lng, max_lat, max_lng)):
        bbox = BoundingBox(min_lat=min_lat, min_lng=min_lng, max_lat=max_lat, max_lng=max_lng)  # type: ignore[arg-type]

    places = await offline_manager.list_cached_places(bbox=bbox)
    return {"places": places, "total": len(places)}


@router.delete("/api/offline/places/{place_id}")
async def delete_cached_place(place_id: str):
    """Remove a place from offline cache."""
    try:
        deleted = await offline_manager.delete_cached_place(place_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid place ID") from exc
    if not deleted:
        raise HTTPException(404, "Place not found in offline cache")
    return {"status": "deleted", "place_id": place_id}


@router.delete("/api/offline/places")
async def clear_cached_places():
    """Clear all cached places."""
    count = await offline_manager.clear_cached_places()
    return {"status": "cleared", "removed_count": count}


# ------------------------------------------------------------------
# Sync queue
# ------------------------------------------------------------------


@router.get("/api/offline/sync/queue", response_model=SyncQueueResponse)
async def get_sync_queue():
    """Get pending sync items."""
    items = await offline_manager.list_queue(include_synced=False)
    return SyncQueueResponse(items=items, total=len(items))


@router.post("/api/offline/sync", response_model=SyncResult)
async def sync_pending(req: SyncRequest):
    """Sync all pending offline operations to the server."""
    result = await offline_manager.sync_all(conflict_strategy=req.resolve_conflicts)
    return result


@router.delete("/api/offline/sync/queue/{item_id}")
async def delete_sync_item(item_id: str):
    """Remove a specific item from the sync queue."""
    try:
        deleted = await offline_manager.delete_queue_item(item_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid item ID") from exc
    if not deleted:
        raise HTTPException(404, "Sync queue item not found")
    return {"status": "deleted", "item_id": item_id}


@router.post("/api/offline/sync/queue/clear-synced")
async def clear_synced_items():
    """Remove all already-synced items from the queue."""
    count = await offline_manager.clear_synced()
    return {"status": "cleared", "removed_count": count}


# ------------------------------------------------------------------
# Offline navigation (compass mode)
# ------------------------------------------------------------------


@router.post("/api/offline/navigate", response_model=OfflineNavResponse)
async def navigate_to_target(req: OfflineNavRequest):
    """Compute compass bearing and distance to a target (works offline)."""
    target_coords = req.target_coordinates
    target_name = None

    if req.target_place_id:
        cp = await offline_manager.get_cached_place(req.target_place_id)
        if not cp:
            raise HTTPException(404, "Target place not found in offline cache")
        target_coords = cp.place.coordinates
        target_name = cp.place.name

    if not target_coords:
        raise HTTPException(400, "Either target_place_id or target_coordinates is required")

    nav = await offline_manager.compute_navigation(
        current=req.current_position,
        target=target_coords,
        target_name=target_name,
    )
    return OfflineNavResponse(**nav)


@router.post("/api/offline/navigate/nearest")
async def navigate_to_nearest(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """Find and navigate to the nearest cached place."""
    position = Coordinates(lat=lat, lng=lng)
    nearest = await offline_manager.find_nearest_cached(position)
    if not nearest:
        raise HTTPException(404, "No cached places available")

    nav = await offline_manager.compute_navigation(
        current=position,
        target=nearest.place.coordinates,
        target_name=nearest.place.name,
    )
    return OfflineNavResponse(**nav)


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------


@router.get("/api/offline/status", response_model=OfflineStatus)
async def offline_status():
    """Get overall offline mode status and disk usage."""
    return await offline_manager.status()


@router.get("/api/offline/connectivity")
async def connectivity_check():
    """Check if the device has network connectivity."""
    is_online = await offline_manager.check_connectivity()
    return {"is_online": is_online}
