"""Offline mode models (Epic 7): tile regions, cached places, sync queue."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.place import Coordinates, Place, PlaceCategory


class BoundingBox(BaseModel):
    """Geographic bounding box for region selection."""

    min_lat: float = Field(..., ge=-90, le=90)
    min_lng: float = Field(..., ge=-180, le=180)
    max_lat: float = Field(..., ge=-90, le=90)
    max_lng: float = Field(..., ge=-180, le=180)


class TileSource(enum.StrEnum):
    """Supported vector tile sources."""

    OSM = "osm"
    OPENMAPTILES = "openmaptiles"


class OfflineRegion(BaseModel):
    """A downloaded map region with tile metadata."""

    region_id: str
    name: str
    bbox: BoundingBox
    tile_source: TileSource = TileSource.OSM
    min_zoom: int = Field(0, ge=0, le=20)
    max_zoom: int = Field(14, ge=0, le=20)
    tile_count: int = Field(0, ge=0)
    disk_usage_bytes: int = Field(0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = Field("pending", pattern=r"^(pending|downloading|ready|error)$")
    progress_percent: float = Field(0.0, ge=0, le=100)


class CachedPlace(BaseModel):
    """A place cached for offline access."""

    place: Place
    cached_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    has_llm_description: bool = False
    data_version: int = Field(1, ge=1, description="Version for cache staleness detection")


class SyncOperation(enum.StrEnum):
    """Types of offline operations pending sync."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class SyncResourceType(enum.StrEnum):
    """Resource types that can be queued for sync."""

    VISIT = "visit"
    NOTE = "note"
    RATING = "rating"
    FOG_REVEAL = "fog_reveal"
    PHOTO = "photo"


class SyncQueueItem(BaseModel):
    """A pending operation to sync when connectivity is restored."""

    id: str
    operation: SyncOperation
    resource_type: SyncResourceType
    resource_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    retry_count: int = Field(0, ge=0)
    last_error: str | None = None
    synced: bool = False
    synced_at: datetime | None = None


class SyncConflict(BaseModel):
    """A conflict detected during sync between local and server state."""

    item_id: str
    resource_type: SyncResourceType
    local_data: dict[str, Any]
    server_data: dict[str, Any]
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SyncResult(BaseModel):
    """Result of a sync operation."""

    synced_count: int = 0
    failed_count: int = 0
    conflict_count: int = 0
    conflicts: list[SyncConflict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class OfflineStatus(BaseModel):
    """Overall offline mode status summary."""

    regions_count: int = 0
    cached_places_count: int = 0
    pending_sync_count: int = 0
    total_disk_usage_bytes: int = 0
    storage_limit_bytes: int = 0
    storage_used_percent: float = 0.0
    is_online: bool = True
    last_sync_at: datetime | None = None


# --- API Request/Response models ---


class TileDownloadRequest(BaseModel):
    """POST /api/offline/tiles/download — request to download tiles for a region."""

    name: str = Field(..., min_length=1, max_length=200, description="Region display name")
    bbox: BoundingBox
    tile_source: TileSource = TileSource.OSM
    min_zoom: int = Field(0, ge=0, le=20)
    max_zoom: int = Field(14, ge=0, le=18, description="Max zoom level (capped at 18)")


class TileDownloadResponse(BaseModel):
    """Response after initiating tile download."""

    region: OfflineRegion
    estimated_tile_count: int
    estimated_size_mb: float


class RegionListResponse(BaseModel):
    """GET /api/offline/tiles/regions — list downloaded regions."""

    regions: list[OfflineRegion]
    total_disk_usage_bytes: int


class CachePlacesRequest(BaseModel):
    """POST /api/offline/places/cache — cache places for offline use."""

    bbox: BoundingBox
    categories: list[PlaceCategory] = Field(default_factory=list)
    include_descriptions: bool = Field(True, description="Pre-fetch LLM descriptions")
    route_id: str | None = Field(None, description="Cache all places from a planned route")


class CachePlacesResponse(BaseModel):
    """Response after caching places."""

    cached_count: int
    total_cached: int
    disk_usage_bytes: int


class OfflineNavRequest(BaseModel):
    """POST /api/offline/navigate — compass navigation to a point."""

    current_position: Coordinates
    target_place_id: str | None = None
    target_coordinates: Coordinates | None = None


class OfflineNavResponse(BaseModel):
    """Compass-style navigation response."""

    bearing_deg: float = Field(..., ge=0, lt=360, description="Compass bearing to target")
    distance_m: float = Field(..., ge=0, description="Straight-line distance in meters")
    target_name: str | None = None
    target_coordinates: Coordinates


class SyncRequest(BaseModel):
    """POST /api/offline/sync — sync pending operations."""

    force: bool = Field(False, description="Force sync even if recently synced")
    resolve_conflicts: str = Field(
        "local_wins",
        pattern=r"^(local_wins|server_wins|manual)$",
        description="Conflict resolution strategy",
    )


class SyncQueueResponse(BaseModel):
    """GET /api/offline/sync/queue — pending sync items."""

    items: list[SyncQueueItem]
    total: int
