"""Offline mode tests (Epic 7): tiles, place cache, sync queue, navigation."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.offline import BoundingBox, SyncOperation, SyncResourceType
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.offline_manager import (
    _bearing,
    _estimate_tile_count,
    _haversine,
    offline_manager,
)


@pytest.fixture(autouse=True)
def reset_offline():
    """Reset offline data before and after each test."""
    offline_manager._reset_store()
    yield
    offline_manager._reset_store()


@pytest.fixture
def bbox_kotor() -> BoundingBox:
    """Bounding box around Kotor, Montenegro."""
    return BoundingBox(min_lat=42.40, min_lng=18.70, max_lat=42.50, max_lng=18.80)


@pytest.fixture
def sample_places_for_cache() -> list[Place]:
    """Places inside the Kotor bounding box."""
    return [
        Place(
            id=f"offline_{i}",
            source=PlaceSource.OSM,
            name=f"Offline Place {i}",
            description=f"Description for place {i}",
            categories=[PlaceCategory.RUINS],
            coordinates=Coordinates(lat=42.42 + i * 0.01, lng=18.72 + i * 0.01),
            confidence=0.7,
        )
        for i in range(5)
    ]


# ------------------------------------------------------------------
# Unit tests: helper functions
# ------------------------------------------------------------------


class TestHaversine:
    def test_same_point(self):
        assert _haversine(42.0, 18.0, 42.0, 18.0) == 0.0

    def test_known_distance(self):
        # Kotor to Dubrovnik ~61 km (straight line)
        dist = _haversine(42.4247, 18.7712, 42.6507, 18.0944)
        assert 55_000 < dist < 65_000

    def test_equator(self):
        # 1 degree at equator ~111 km
        dist = _haversine(0.0, 0.0, 0.0, 1.0)
        assert 110_000 < dist < 112_000


class TestBearing:
    def test_north(self):
        b = _bearing(42.0, 18.0, 43.0, 18.0)
        assert 355 < b or b < 5  # approximately 0°

    def test_east(self):
        b = _bearing(0.0, 0.0, 0.0, 1.0)
        assert 85 < b < 95  # approximately 90°

    def test_south(self):
        b = _bearing(43.0, 18.0, 42.0, 18.0)
        assert 175 < b < 185  # approximately 180°


class TestTileEstimate:
    def test_small_area(self):
        bbox = BoundingBox(min_lat=42.4, min_lng=18.7, max_lat=42.5, max_lng=18.8)
        count = _estimate_tile_count(bbox, 0, 10)
        assert count > 0

    def test_single_zoom(self):
        bbox = BoundingBox(min_lat=42.4, min_lng=18.7, max_lat=42.5, max_lng=18.8)
        count = _estimate_tile_count(bbox, 5, 5)
        assert count > 0

    def test_higher_zoom_more_tiles(self):
        bbox = BoundingBox(min_lat=42.4, min_lng=18.7, max_lat=42.5, max_lng=18.8)
        low = _estimate_tile_count(bbox, 0, 5)
        high = _estimate_tile_count(bbox, 0, 10)
        assert high > low


# ------------------------------------------------------------------
# Unit tests: OfflineManager service
# ------------------------------------------------------------------


class TestRegionManager:
    async def test_create_region(self, bbox_kotor):
        region, tile_count, est_mb = await offline_manager.create_region(
            name="Kotor Bay", bbox=bbox_kotor, min_zoom=0, max_zoom=10
        )
        assert region.region_id
        assert region.name == "Kotor Bay"
        assert region.status == "ready"
        assert tile_count > 0
        assert est_mb > 0

    async def test_list_regions(self, bbox_kotor):
        await offline_manager.create_region(name="R1", bbox=bbox_kotor)
        await offline_manager.create_region(name="R2", bbox=bbox_kotor)
        regions = await offline_manager.list_regions()
        assert len(regions) == 2

    async def test_get_region(self, bbox_kotor):
        region, _, _ = await offline_manager.create_region(name="Test", bbox=bbox_kotor)
        found = await offline_manager.get_region(region.region_id)
        assert found is not None
        assert found.name == "Test"

    async def test_get_region_not_found(self):
        assert await offline_manager.get_region("nonexistent") is None

    async def test_delete_region(self, bbox_kotor):
        region, _, _ = await offline_manager.create_region(name="Delete Me", bbox=bbox_kotor)
        assert await offline_manager.delete_region(region.region_id) is True
        assert await offline_manager.get_region(region.region_id) is None

    async def test_delete_nonexistent(self):
        assert await offline_manager.delete_region("nope") is False


class TestPlaceCache:
    async def test_cache_single_place(self, sample_places_for_cache):
        place = sample_places_for_cache[0]
        cached = await offline_manager.cache_place(place)
        assert cached.place.id == place.id
        assert cached.has_llm_description is False

    async def test_cache_multiple_places(self, sample_places_for_cache):
        count = await offline_manager.cache_places(sample_places_for_cache)
        assert count == 5

    async def test_get_cached_place(self, sample_places_for_cache):
        place = sample_places_for_cache[0]
        await offline_manager.cache_place(place)
        found = await offline_manager.get_cached_place(place.id)
        assert found is not None
        assert found.place.name == place.name

    async def test_get_cached_place_not_found(self):
        assert await offline_manager.get_cached_place("nope") is None

    async def test_list_cached_places(self, sample_places_for_cache):
        await offline_manager.cache_places(sample_places_for_cache)
        all_places = await offline_manager.list_cached_places()
        assert len(all_places) == 5

    async def test_list_cached_places_bbox_filter(self, sample_places_for_cache):
        await offline_manager.cache_places(sample_places_for_cache)
        bbox = BoundingBox(min_lat=42.42, min_lng=18.72, max_lat=42.44, max_lng=18.74)
        filtered = await offline_manager.list_cached_places(bbox=bbox)
        assert 0 < len(filtered) < 5

    async def test_delete_cached_place(self, sample_places_for_cache):
        place = sample_places_for_cache[0]
        await offline_manager.cache_place(place)
        assert await offline_manager.delete_cached_place(place.id) is True
        assert await offline_manager.get_cached_place(place.id) is None

    async def test_clear_cached_places(self, sample_places_for_cache):
        await offline_manager.cache_places(sample_places_for_cache)
        count = await offline_manager.clear_cached_places()
        assert count == 5
        assert len(await offline_manager.list_cached_places()) == 0


class TestSyncQueue:
    async def test_enqueue(self):
        item = await offline_manager.enqueue(
            operation=SyncOperation.CREATE,
            resource_type=SyncResourceType.VISIT,
            resource_id="visit_1",
            data={"place_id": "place_1", "status": "visited"},
        )
        assert item.id
        assert item.synced is False

    async def test_list_queue(self):
        await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        await offline_manager.enqueue(SyncOperation.UPDATE, SyncResourceType.NOTE, "n1")
        items = await offline_manager.list_queue()
        assert len(items) == 2

    async def test_mark_synced(self):
        item = await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        assert await offline_manager.mark_synced(item.id) is True
        updated = await offline_manager.get_queue_item(item.id)
        assert updated is not None
        assert updated.synced is True
        assert updated.synced_at is not None

    async def test_mark_synced_not_found(self):
        assert await offline_manager.mark_synced("nope") is False

    async def test_delete_queue_item(self):
        item = await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        assert await offline_manager.delete_queue_item(item.id) is True
        assert await offline_manager.get_queue_item(item.id) is None

    async def test_sync_all(self):
        await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.NOTE, "n1")
        result = await offline_manager.sync_all()
        assert result.synced_count == 2
        assert result.failed_count == 0

    async def test_clear_synced(self):
        item = await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        await offline_manager.mark_synced(item.id)
        count = await offline_manager.clear_synced()
        assert count == 1

    async def test_pending_excludes_synced(self):
        item = await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        await offline_manager.mark_synced(item.id)
        pending = await offline_manager.list_queue(include_synced=False)
        assert len(pending) == 0


class TestNavigation:
    async def test_compute_navigation(self):
        current = Coordinates(lat=42.42, lng=18.77)
        target = Coordinates(lat=42.45, lng=18.77)
        nav = await offline_manager.compute_navigation(current, target, "Fortress")
        assert nav["distance_m"] > 0
        assert 0 <= nav["bearing_deg"] < 360
        assert nav["target_name"] == "Fortress"

    async def test_find_nearest_cached(self, sample_places_for_cache):
        await offline_manager.cache_places(sample_places_for_cache)
        position = Coordinates(lat=42.425, lng=18.725)
        nearest = await offline_manager.find_nearest_cached(position)
        assert nearest is not None
        # offline_0 at (42.42, 18.72) or offline_1 at (42.43, 18.73) — both close
        assert nearest.place.id in ("offline_0", "offline_1")

    async def test_find_nearest_no_cache(self):
        position = Coordinates(lat=42.42, lng=18.77)
        assert await offline_manager.find_nearest_cached(position) is None


class TestIdValidation:
    async def test_valid_id(self):
        offline_manager._validate_id("abc123")
        offline_manager._validate_id("my-region_01")

    async def test_invalid_id_path_traversal(self):
        with pytest.raises(ValueError):
            offline_manager._validate_id("../../../etc/passwd")

    async def test_invalid_id_empty(self):
        with pytest.raises(ValueError):
            offline_manager._validate_id("")

    async def test_invalid_id_special_chars(self):
        with pytest.raises(ValueError):
            offline_manager._validate_id("id with spaces")

    async def test_get_region_invalid_id(self):
        with pytest.raises(ValueError):
            await offline_manager.get_region("../bad")

    async def test_get_place_invalid_id(self):
        with pytest.raises(ValueError):
            await offline_manager.get_cached_place("../bad")

    async def test_delete_queue_invalid_id(self):
        with pytest.raises(ValueError):
            await offline_manager.delete_queue_item("../bad")


class TestStorageLimit:
    async def test_check_storage_within_limit(self):
        # Should not raise for small amounts
        await offline_manager._check_storage_limit(1024)

    async def test_current_disk_usage_empty(self):
        usage = await offline_manager._current_disk_usage()
        assert usage == 0

    async def test_current_disk_usage_with_data(self, sample_places_for_cache):
        await offline_manager.cache_places(sample_places_for_cache)
        usage = await offline_manager._current_disk_usage()
        assert usage > 0


class TestConnectivity:
    async def test_connectivity_returns_bool(self):
        # May be True or False depending on network, but must not crash
        result = await offline_manager.check_connectivity()
        assert isinstance(result, bool)


class TestOfflineStatus:
    async def test_empty_status(self):
        status = await offline_manager.status()
        assert status.regions_count == 0
        assert status.cached_places_count == 0
        assert status.pending_sync_count == 0

    async def test_status_with_data(self, bbox_kotor, sample_places_for_cache):
        await offline_manager.create_region(name="R", bbox=bbox_kotor)
        await offline_manager.cache_places(sample_places_for_cache)
        await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        status = await offline_manager.status()
        assert status.regions_count == 1
        assert status.cached_places_count == 5
        assert status.pending_sync_count == 1


# ------------------------------------------------------------------
# API integration tests
# ------------------------------------------------------------------


@pytest.fixture
def api_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestTilesAPI:
    async def test_download_tiles(self, api_client):
        async with api_client as client:
            resp = await client.post("/api/offline/tiles/download", json={
                "name": "Kotor",
                "bbox": {"min_lat": 42.4, "min_lng": 18.7, "max_lat": 42.5, "max_lng": 18.8},
                "min_zoom": 0,
                "max_zoom": 10,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["region"]["name"] == "Kotor"
        assert data["estimated_tile_count"] > 0

    async def test_download_tiles_invalid_bbox(self, api_client):
        async with api_client as client:
            resp = await client.post("/api/offline/tiles/download", json={
                "name": "Bad",
                "bbox": {"min_lat": 43.0, "min_lng": 18.7, "max_lat": 42.0, "max_lng": 18.8},
            })
        assert resp.status_code == 400

    async def test_list_regions(self, api_client, bbox_kotor):
        await offline_manager.create_region(name="R1", bbox=bbox_kotor)
        async with api_client as client:
            resp = await client.get("/api/offline/tiles/regions")
        assert resp.status_code == 200
        assert len(resp.json()["regions"]) == 1

    async def test_get_region(self, api_client, bbox_kotor):
        region, _, _ = await offline_manager.create_region(name="R1", bbox=bbox_kotor)
        async with api_client as client:
            resp = await client.get(f"/api/offline/tiles/regions/{region.region_id}")
        assert resp.status_code == 200

    async def test_get_region_404(self, api_client):
        async with api_client as client:
            resp = await client.get("/api/offline/tiles/regions/nonexistent")
        assert resp.status_code == 404

    async def test_delete_region(self, api_client, bbox_kotor):
        region, _, _ = await offline_manager.create_region(name="R1", bbox=bbox_kotor)
        async with api_client as client:
            resp = await client.delete(f"/api/offline/tiles/regions/{region.region_id}")
        assert resp.status_code == 200

    async def test_delete_region_404(self, api_client):
        async with api_client as client:
            resp = await client.delete("/api/offline/tiles/regions/nonexistent")
        assert resp.status_code == 404


class TestPlacesAPI:
    async def test_get_cached_place(self, api_client, sample_places_for_cache):
        place = sample_places_for_cache[0]
        await offline_manager.cache_place(place)
        async with api_client as client:
            resp = await client.get(f"/api/offline/places/{place.id}")
        assert resp.status_code == 200
        assert resp.json()["place"]["id"] == place.id

    async def test_get_cached_place_404(self, api_client):
        async with api_client as client:
            resp = await client.get("/api/offline/places/nonexistent")
        assert resp.status_code == 404

    async def test_list_cached_places(self, api_client, sample_places_for_cache):
        await offline_manager.cache_places(sample_places_for_cache)
        async with api_client as client:
            resp = await client.get("/api/offline/places")
        assert resp.status_code == 200
        assert resp.json()["total"] == 5

    async def test_list_cached_places_with_bbox(self, api_client, sample_places_for_cache):
        await offline_manager.cache_places(sample_places_for_cache)
        async with api_client as client:
            resp = await client.get("/api/offline/places", params={
                "min_lat": 42.42, "min_lng": 18.72,
                "max_lat": 42.44, "max_lng": 18.74,
            })
        assert resp.status_code == 200
        assert 0 < resp.json()["total"] < 5

    async def test_delete_cached_place(self, api_client, sample_places_for_cache):
        place = sample_places_for_cache[0]
        await offline_manager.cache_place(place)
        async with api_client as client:
            resp = await client.delete(f"/api/offline/places/{place.id}")
        assert resp.status_code == 200

    async def test_clear_cached_places(self, api_client, sample_places_for_cache):
        await offline_manager.cache_places(sample_places_for_cache)
        async with api_client as client:
            resp = await client.delete("/api/offline/places")
        assert resp.status_code == 200
        assert resp.json()["removed_count"] == 5


class TestSyncAPI:
    async def test_get_sync_queue(self, api_client):
        await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        async with api_client as client:
            resp = await client.get("/api/offline/sync/queue")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_sync_pending(self, api_client):
        await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        async with api_client as client:
            resp = await client.post("/api/offline/sync", json={})
        assert resp.status_code == 200
        assert resp.json()["synced_count"] == 1

    async def test_delete_sync_item(self, api_client):
        item = await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        async with api_client as client:
            resp = await client.delete(f"/api/offline/sync/queue/{item.id}")
        assert resp.status_code == 200

    async def test_delete_sync_item_404(self, api_client):
        async with api_client as client:
            resp = await client.delete("/api/offline/sync/queue/nonexistent")
        assert resp.status_code == 404

    async def test_clear_synced(self, api_client):
        item = await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        await offline_manager.mark_synced(item.id)
        async with api_client as client:
            resp = await client.post("/api/offline/sync/queue/clear-synced")
        assert resp.status_code == 200
        assert resp.json()["removed_count"] == 1


class TestNavigationAPI:
    async def test_navigate_to_coordinates(self, api_client):
        async with api_client as client:
            resp = await client.post("/api/offline/navigate", json={
                "current_position": {"lat": 42.42, "lng": 18.77},
                "target_coordinates": {"lat": 42.45, "lng": 18.77},
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["distance_m"] > 0
        assert 0 <= data["bearing_deg"] < 360

    async def test_navigate_to_place(self, api_client, sample_places_for_cache):
        place = sample_places_for_cache[0]
        await offline_manager.cache_place(place)
        async with api_client as client:
            resp = await client.post("/api/offline/navigate", json={
                "current_position": {"lat": 42.42, "lng": 18.77},
                "target_place_id": place.id,
            })
        assert resp.status_code == 200
        assert resp.json()["target_name"] == place.name

    async def test_navigate_to_missing_place(self, api_client):
        async with api_client as client:
            resp = await client.post("/api/offline/navigate", json={
                "current_position": {"lat": 42.42, "lng": 18.77},
                "target_place_id": "nonexistent",
            })
        assert resp.status_code == 404

    async def test_navigate_no_target(self, api_client):
        async with api_client as client:
            resp = await client.post("/api/offline/navigate", json={
                "current_position": {"lat": 42.42, "lng": 18.77},
            })
        assert resp.status_code == 400

    async def test_navigate_nearest(self, api_client, sample_places_for_cache):
        await offline_manager.cache_places(sample_places_for_cache)
        async with api_client as client:
            resp = await client.post("/api/offline/navigate/nearest?lat=42.425&lng=18.725")
        assert resp.status_code == 200
        assert resp.json()["target_name"] is not None

    async def test_navigate_nearest_no_cache(self, api_client):
        async with api_client as client:
            resp = await client.post("/api/offline/navigate/nearest?lat=42.42&lng=18.77")
        assert resp.status_code == 404


class TestStatusAPI:
    async def test_status_empty(self, api_client):
        async with api_client as client:
            resp = await client.get("/api/offline/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["regions_count"] == 0
        assert data["cached_places_count"] == 0
        assert data["pending_sync_count"] == 0
        assert "storage_limit_bytes" in data
        assert "storage_used_percent" in data
        assert "is_online" in data

    async def test_status_with_data(self, api_client, bbox_kotor, sample_places_for_cache):
        await offline_manager.create_region(name="R", bbox=bbox_kotor)
        await offline_manager.cache_places(sample_places_for_cache)
        await offline_manager.enqueue(SyncOperation.CREATE, SyncResourceType.VISIT, "v1")
        async with api_client as client:
            resp = await client.get("/api/offline/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["regions_count"] == 1
        assert data["cached_places_count"] == 5
        assert data["pending_sync_count"] == 1

    async def test_connectivity_endpoint(self, api_client):
        async with api_client as client:
            resp = await client.get("/api/offline/connectivity")
        assert resp.status_code == 200
        assert "is_online" in resp.json()


class TestInvalidIdAPI:
    async def test_get_region_invalid(self, api_client):
        async with api_client as client:
            resp = await client.get("/api/offline/tiles/regions/../bad")
        # FastAPI may normalize the path or our handler catches it
        assert resp.status_code in (400, 404, 422)

    async def test_get_place_invalid(self, api_client):
        async with api_client as client:
            resp = await client.get("/api/offline/places/../bad")
        assert resp.status_code in (400, 404, 422)

    async def test_delete_sync_invalid(self, api_client):
        async with api_client as client:
            resp = await client.delete("/api/offline/sync/queue/../bad")
        assert resp.status_code in (400, 404, 422)
