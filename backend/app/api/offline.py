"""Offline mode API endpoints (Epic 7): tiles, place cache, sync, navigation."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

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
from app.models.place import Coordinates
from app.services.offline_manager import offline_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["offline"])


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

    region, tile_count, est_mb = await offline_manager.create_region(
        name=req.name,
        bbox=req.bbox,
        tile_source=req.tile_source,
        min_zoom=req.min_zoom,
        max_zoom=req.max_zoom,
    )
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
    except ValueError:
        raise HTTPException(400, "Invalid region ID")
    if not region:
        raise HTTPException(404, "Region not found")
    return region


@router.delete("/api/offline/tiles/regions/{region_id}")
async def delete_region(region_id: str):
    """Delete a downloaded region and free storage."""
    try:
        deleted = await offline_manager.delete_region(region_id)
    except ValueError:
        raise HTTPException(400, "Invalid region ID")
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
        cached_count = await offline_manager.cache_route_places(req.route_id)
        all_cached = await offline_manager.list_cached_places()
        total_size = sum(
            offline_manager._place_path(cp.place.id).stat().st_size
            for cp in all_cached
            if offline_manager._place_path(cp.place.id).exists()
        )
        return CachePlacesResponse(
            cached_count=cached_count, total_cached=len(all_cached), disk_usage_bytes=total_size
        )

    # BBox-based caching: discover places and cache them
    from app.services.offline_manager import _haversine

    center_lat = (req.bbox.min_lat + req.bbox.max_lat) / 2
    center_lng = (req.bbox.min_lng + req.bbox.max_lng) / 2
    radius_m = _haversine(req.bbox.min_lat, req.bbox.min_lng,
                          req.bbox.max_lat, req.bbox.max_lng) / 2
    radius_km = min(radius_m / 1000, 50.0)

    try:
        from app.services.discovery import discover_places_from_sources

        places = await discover_places_from_sources(
            lat=center_lat,
            lng=center_lng,
            radius_km=radius_km,
            categories=[c.value for c in req.categories] if req.categories else None,
        )
    except Exception:
        # Graceful degradation: if offline, return what's already cached in the bbox
        logger.warning("Discovery failed (possibly offline), returning existing cache")
        places = []

    cached_count = await offline_manager.cache_places(
        places, has_llm_description=req.include_descriptions
    )

    all_cached = await offline_manager.list_cached_places()
    total_size = sum(
        offline_manager._place_path(cp.place.id).stat().st_size
        for cp in all_cached
        if offline_manager._place_path(cp.place.id).exists()
    )

    return CachePlacesResponse(
        cached_count=cached_count,
        total_cached=len(all_cached),
        disk_usage_bytes=total_size,
    )


@router.get("/api/offline/places/{place_id}")
async def get_cached_place(place_id: str):
    """Retrieve a cached place by ID."""
    try:
        cp = await offline_manager.get_cached_place(place_id)
    except ValueError:
        raise HTTPException(400, "Invalid place ID")
    if not cp:
        raise HTTPException(404, "Place not found in offline cache")
    return cp


@router.get("/api/offline/places")
async def list_cached_places(
    min_lat: float | None = None,
    min_lng: float | None = None,
    max_lat: float | None = None,
    max_lng: float | None = None,
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
    except ValueError:
        raise HTTPException(400, "Invalid place ID")
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
    except ValueError:
        raise HTTPException(400, "Invalid item ID")
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
async def navigate_to_nearest(lat: float, lng: float):
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
