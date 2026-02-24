"""Offline mode manager service (Epic 7): regions, place cache, sync queue, navigation."""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.models.offline import (
    BoundingBox,
    CachedPlace,
    OfflineRegion,
    OfflineStatus,
    SyncOperation,
    SyncQueueItem,
    SyncResourceType,
    SyncResult,
    TileSource,
)
from app.models.place import Coordinates, Place

logger = logging.getLogger(__name__)

# Only allow alphanumeric, hyphens, underscores in resource IDs
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate distance in meters between two points using haversine formula."""
    earth_radius = 6_371_000  # meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return earth_radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate initial bearing from point 1 to point 2 in degrees (0-360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lng2 - lng1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def _estimate_tile_count(bbox: BoundingBox, min_zoom: int, max_zoom: int) -> int:
    """Estimate number of tiles needed for a bounding box across zoom levels."""
    total = 0
    for z in range(min_zoom, max_zoom + 1):
        n = 2 ** z
        x_min = int((bbox.min_lng + 180) / 360 * n)
        x_max = int((bbox.max_lng + 180) / 360 * n)
        y_min = int(
            (1 - math.log(math.tan(math.radians(bbox.max_lat))
             + 1 / math.cos(math.radians(bbox.max_lat))) / math.pi) / 2 * n
        )
        y_max = int(
            (1 - math.log(math.tan(math.radians(bbox.min_lat))
             + 1 / math.cos(math.radians(bbox.min_lat))) / math.pi) / 2 * n
        )
        total += max(0, (x_max - x_min + 1)) * max(0, (y_max - y_min + 1))
    return total


class OfflineManager:
    """Manages offline regions, cached places, and sync queue on disk."""

    def __init__(self, data_dir: str | None = None) -> None:
        self._base = Path(data_dir or settings.offline_data_dir)
        self._regions_dir = self._base / "regions"
        self._places_dir = self._base / "places"
        self._queue_dir = self._base / "sync_queue"
        self._meta_file = self._base / "meta.json"
        for d in (self._regions_dir, self._places_dir, self._queue_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # ID validation (path traversal protection)
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_id(resource_id: str) -> None:
        """Raise ValueError if resource_id contains unsafe characters."""
        if not _SAFE_ID_RE.match(resource_id):
            raise ValueError(f"Invalid resource ID: {resource_id!r}")

    # ------------------------------------------------------------------
    # Storage limit enforcement
    # ------------------------------------------------------------------

    def _storage_limit_bytes(self) -> int:
        return settings.offline_storage_limit_mb * 1024 * 1024

    async def _current_disk_usage(self) -> int:
        """Calculate total disk usage of all offline data."""
        total = 0
        for d in (self._regions_dir, self._places_dir, self._queue_dir):
            for f in d.glob("*.json"):
                total += f.stat().st_size
        return total

    async def _check_storage_limit(self, additional_bytes: int = 0) -> None:
        """Raise ValueError if adding data would exceed storage limit."""
        current = await self._current_disk_usage()
        limit = self._storage_limit_bytes()
        if current + additional_bytes > limit:
            raise ValueError(
                f"Storage limit exceeded: {current + additional_bytes} > {limit} bytes"
            )

    # ------------------------------------------------------------------
    # Connectivity check
    # ------------------------------------------------------------------

    async def check_connectivity(self) -> bool:
        """Check if server is reachable (basic connectivity probe)."""
        try:
            from app.utils.http_client import get_http_client
            client = await get_http_client()
            resp = await client.get("https://overpass-api.de/api/status", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Region management
    # ------------------------------------------------------------------

    def _region_path(self, region_id: str) -> Path:
        return self._regions_dir / f"{region_id}.json"

    async def create_region(
        self,
        name: str,
        bbox: BoundingBox,
        tile_source: TileSource = TileSource.OSM,
        min_zoom: int = 0,
        max_zoom: int = 14,
    ) -> tuple[OfflineRegion, int, float]:
        """Create a new offline region. Returns (region, tile_count, est_size_mb)."""
        region_id = uuid.uuid4().hex[:12]
        tile_count = _estimate_tile_count(bbox, min_zoom, max_zoom)
        est_size_mb = round(tile_count * 15 / 1024, 2)  # ~15 KB avg per vector tile

        region = OfflineRegion(
            region_id=region_id,
            name=name,
            bbox=bbox,
            tile_source=tile_source,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            tile_count=tile_count,
            disk_usage_bytes=0,
            status="ready",
            progress_percent=100.0,
        )
        self._region_path(region_id).write_text(
            region.model_dump_json(indent=2), encoding="utf-8"
        )
        logger.info(
            "Created offline region %s (%s tiles, ~%.1f MB)", region_id, tile_count, est_size_mb
        )
        return region, tile_count, est_size_mb

    async def list_regions(self) -> list[OfflineRegion]:
        """List all saved offline regions."""
        regions: list[OfflineRegion] = []
        for f in sorted(self._regions_dir.glob("*.json")):
            try:
                regions.append(OfflineRegion.model_validate_json(f.read_text("utf-8")))
            except Exception:
                logger.warning("Corrupted region file: %s", f)
        return regions

    async def get_region(self, region_id: str) -> OfflineRegion | None:
        self._validate_id(region_id)
        path = self._region_path(region_id)
        if not path.exists():
            return None
        return OfflineRegion.model_validate_json(path.read_text("utf-8"))

    async def delete_region(self, region_id: str) -> bool:
        self._validate_id(region_id)
        path = self._region_path(region_id)
        if not path.exists():
            return False
        path.unlink()
        logger.info("Deleted offline region %s", region_id)
        return True

    async def update_region(self, region: OfflineRegion) -> None:
        self._region_path(region.region_id).write_text(
            region.model_dump_json(indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Place cache
    # ------------------------------------------------------------------

    def _place_path(self, place_id: str) -> Path:
        return self._places_dir / f"{place_id}.json"

    async def cache_place(self, place: Place, has_llm_description: bool = False) -> CachedPlace:
        """Cache a single place for offline access."""
        self._validate_id(place.id)
        await self._check_storage_limit(2048)  # estimate ~2KB per place
        cached = CachedPlace(
            place=place,
            has_llm_description=has_llm_description,
        )
        self._place_path(place.id).write_text(
            cached.model_dump_json(indent=2), encoding="utf-8"
        )
        return cached

    async def cache_places(
        self, places: list[Place], has_llm_description: bool = False
    ) -> int:
        """Cache multiple places. Returns count cached."""
        for p in places:
            await self.cache_place(p, has_llm_description)
        return len(places)

    async def get_cached_place(self, place_id: str) -> CachedPlace | None:
        self._validate_id(place_id)
        path = self._place_path(place_id)
        if not path.exists():
            return None
        try:
            return CachedPlace.model_validate_json(path.read_text("utf-8"))
        except Exception:
            return None

    async def list_cached_places(
        self,
        bbox: BoundingBox | None = None,
        categories: list[str] | None = None,
    ) -> list[CachedPlace]:
        """List cached places, optionally filtered by bbox and categories."""
        result: list[CachedPlace] = []
        for f in self._places_dir.glob("*.json"):
            try:
                cp = CachedPlace.model_validate_json(f.read_text("utf-8"))
            except Exception:
                continue
            if bbox:
                c = cp.place.coordinates
                if not (bbox.min_lat <= c.lat <= bbox.max_lat
                        and bbox.min_lng <= c.lng <= bbox.max_lng):
                    continue
            if categories:
                place_cats = {cat.value for cat in cp.place.categories}
                if not place_cats & set(categories):
                    continue
            result.append(cp)
        return result

    async def delete_cached_place(self, place_id: str) -> bool:
        self._validate_id(place_id)
        path = self._place_path(place_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    async def clear_cached_places(self) -> int:
        count = 0
        for f in self._places_dir.glob("*.json"):
            f.unlink(missing_ok=True)
            count += 1
        return count

    async def cache_route_places(self, route_id: str) -> int:
        """Cache all places from a saved route for offline use."""
        # Load route data from the route builder's data dir
        route_data_dir = Path(settings.journal_data_dir).parent / "routes"
        route_file = route_data_dir / f"{route_id}.json"
        if not route_file.exists():
            return 0
        try:
            route_data = json.loads(route_file.read_text("utf-8"))
            places = []
            for wp in route_data.get("waypoints", []):
                place_data = wp.get("place")
                if place_data:
                    places.append(Place.model_validate(place_data))
            return await self.cache_places(places, has_llm_description=True)
        except Exception as exc:
            logger.warning("Failed to cache route %s places: %s", route_id, exc)
            return 0

    # ------------------------------------------------------------------
    # Sync queue
    # ------------------------------------------------------------------

    def _queue_item_path(self, item_id: str) -> Path:
        return self._queue_dir / f"{item_id}.json"

    async def enqueue(
        self,
        operation: SyncOperation,
        resource_type: SyncResourceType,
        resource_id: str,
        data: dict[str, Any] | None = None,
    ) -> SyncQueueItem:
        """Add an operation to the sync queue."""
        item = SyncQueueItem(
            id=uuid.uuid4().hex[:12],
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
            data=data or {},
        )
        self._queue_item_path(item.id).write_text(
            item.model_dump_json(indent=2), encoding="utf-8"
        )
        logger.info("Enqueued sync item %s: %s %s", item.id, operation.value, resource_type.value)
        return item

    async def list_queue(self, include_synced: bool = False) -> list[SyncQueueItem]:
        """List pending sync items (optionally including already synced)."""
        items: list[SyncQueueItem] = []
        for f in sorted(self._queue_dir.glob("*.json")):
            try:
                item = SyncQueueItem.model_validate_json(f.read_text("utf-8"))
            except Exception:
                continue
            if include_synced or not item.synced:
                items.append(item)
        return items

    async def get_queue_item(self, item_id: str) -> SyncQueueItem | None:
        self._validate_id(item_id)
        path = self._queue_item_path(item_id)
        if not path.exists():
            return None
        try:
            return SyncQueueItem.model_validate_json(path.read_text("utf-8"))
        except Exception:
            return None

    async def mark_synced(self, item_id: str) -> bool:
        item = await self.get_queue_item(item_id)
        if not item:
            return False
        item.synced = True
        item.synced_at = datetime.now(UTC)
        self._queue_item_path(item_id).write_text(
            item.model_dump_json(indent=2), encoding="utf-8"
        )
        return True

    async def delete_queue_item(self, item_id: str) -> bool:
        self._validate_id(item_id)
        path = self._queue_item_path(item_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    async def clear_synced(self) -> int:
        """Remove all synced items from queue."""
        count = 0
        for f in self._queue_dir.glob("*.json"):
            try:
                item = SyncQueueItem.model_validate_json(f.read_text("utf-8"))
                if item.synced:
                    f.unlink()
                    count += 1
            except Exception:
                pass
        return count

    async def sync_all(self, conflict_strategy: str = "local_wins") -> SyncResult:
        """Process all pending sync items.

        In a real implementation this would call server APIs.
        For now it marks items as synced (offline-first stub).
        """
        pending = await self.list_queue(include_synced=False)
        result = SyncResult()
        for item in pending:
            if item.synced:
                continue
            try:
                await self.mark_synced(item.id)
                result.synced_count += 1
            except Exception as exc:
                result.failed_count += 1
                result.errors.append(f"{item.id}: {exc}")
        # Persist last-sync timestamp
        self._save_meta({"last_sync_at": datetime.now(UTC).isoformat()})
        return result

    # ------------------------------------------------------------------
    # Navigation helpers (compass-style)
    # ------------------------------------------------------------------

    async def compute_navigation(
        self,
        current: Coordinates,
        target: Coordinates,
        target_name: str | None = None,
    ) -> dict:
        """Compute bearing and distance for compass-mode navigation."""
        dist = _haversine(current.lat, current.lng, target.lat, target.lng)
        bear = _bearing(current.lat, current.lng, target.lat, target.lng)
        return {
            "bearing_deg": round(bear, 1),
            "distance_m": round(dist, 1),
            "target_name": target_name,
            "target_coordinates": target,
        }

    async def find_nearest_cached(self, position: Coordinates) -> CachedPlace | None:
        """Find the nearest cached place to a given position."""
        best: CachedPlace | None = None
        best_dist = float("inf")
        for f in self._places_dir.glob("*.json"):
            try:
                cp = CachedPlace.model_validate_json(f.read_text("utf-8"))
            except Exception:
                continue
            d = _haversine(position.lat, position.lng,
                           cp.place.coordinates.lat, cp.place.coordinates.lng)
            if d < best_dist:
                best_dist = d
                best = cp
        return best

    # ------------------------------------------------------------------
    # Status & meta
    # ------------------------------------------------------------------

    async def status(self) -> OfflineStatus:
        """Get overall offline status."""
        regions = await self.list_regions()
        places = list(self._places_dir.glob("*.json"))
        pending = await self.list_queue(include_synced=False)
        total_bytes = await self._current_disk_usage()
        limit_bytes = self._storage_limit_bytes()
        meta = self._load_meta()
        last_sync = None
        if "last_sync_at" in meta:
            try:
                last_sync = datetime.fromisoformat(meta["last_sync_at"])
            except Exception:
                pass
        used_pct = round(total_bytes / limit_bytes * 100, 1) if limit_bytes > 0 else 0.0
        is_online = await self.check_connectivity()
        return OfflineStatus(
            regions_count=len(regions),
            cached_places_count=len(places),
            pending_sync_count=len(pending),
            total_disk_usage_bytes=total_bytes,
            storage_limit_bytes=limit_bytes,
            storage_used_percent=used_pct,
            is_online=is_online,
            last_sync_at=last_sync,
        )

    def _load_meta(self) -> dict:
        if self._meta_file.exists():
            try:
                return json.loads(self._meta_file.read_text("utf-8"))
            except Exception:
                return {}
        return {}

    def _save_meta(self, data: dict) -> None:
        meta = self._load_meta()
        meta.update(data)
        self._meta_file.write_text(json.dumps(meta, default=str), encoding="utf-8")

    def _reset_store(self) -> None:
        """Reset all offline data (for testing)."""
        import shutil
        for d in (self._regions_dir, self._places_dir, self._queue_dir):
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)
        if self._meta_file.exists():
            self._meta_file.unlink()


# Module-level singleton
offline_manager = OfflineManager()
