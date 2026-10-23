"""Offline mode hardening: path traversal, unbounded regions/caches, wrong math, 500s."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.offline import BoundingBox
from app.models.place import Coordinates, DiscoverResponse, Place, PlaceSource
from app.services.offline_manager import _estimate_tile_count, offline_manager

client = TestClient(app, raise_server_exceptions=False)


class _DownClient:
    async def get(self, url, **kwargs):
        raise ConnectionError("network is down in tests")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    offline_manager._reset_store()
    # Never let a test reach the real network (status()/connectivity call out).
    monkeypatch.setattr("app.utils.http_client.get_http_client", lambda: _DownClient())
    yield
    offline_manager._reset_store()


def _place(pid: str, lat: float = 42.42, lng: float = 18.72) -> Place:
    return Place(id=pid, source=PlaceSource.OSM, name=pid, coordinates=Coordinates(lat=lat, lng=lng))


# ── path traversal via route_id ──────────────────────────────────


class TestRouteIdTraversal:
    @pytest.fixture
    def secret(self, tmp_path, monkeypatch):
        """A Place-shaped JSON file *outside* the routes directory."""
        monkeypatch.setattr(settings, "journal_data_dir", str(tmp_path / "journal"))
        (tmp_path / "routes").mkdir()
        secret = tmp_path / "secret.json"
        secret.write_text(json.dumps({
            "waypoints": [{"place": _place("leaked").model_dump(mode="json")}],
        }))
        return secret

    async def test_service_rejects_traversal_route_id(self, secret):
        with pytest.raises(ValueError):
            await offline_manager.cache_route_places("../secret")
        assert await offline_manager.get_cached_place("leaked") is None

    def test_api_rejects_traversal_route_id(self, secret):
        resp = client.post("/api/offline/places/cache", json={
            "bbox": {"min_lat": 42.4, "min_lng": 18.7, "max_lat": 42.5, "max_lng": 18.8},
            "route_id": "../secret",
        })
        assert resp.status_code in (400, 422)
        assert client.get("/api/offline/places/leaked").status_code == 404


# ── unbounded regions / caches ───────────────────────────────────


class TestRegionLimits:
    def test_world_sized_region_is_refused(self):
        resp = client.post("/api/offline/tiles/download", json={
            "name": "everything",
            "bbox": {"min_lat": -80, "min_lng": -170, "max_lat": 80, "max_lng": 170},
            "min_zoom": 0, "max_zoom": 18,
        })
        assert resp.status_code == 400
        assert "tiles" in resp.json()["detail"].lower()
        assert client.get("/api/offline/tiles/regions").json()["regions"] == []

    async def test_service_refuses_region_over_tile_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "offline_max_region_tiles", 10)
        bbox = BoundingBox(min_lat=42.0, min_lng=18.0, max_lat=43.0, max_lng=19.0)
        with pytest.raises(ValueError, match="tiles"):
            await offline_manager.create_region("big", bbox, min_zoom=0, max_zoom=12)

    async def test_region_within_limit_still_works(self):
        bbox = BoundingBox(min_lat=42.40, min_lng=18.70, max_lat=42.50, max_lng=18.80)
        region, tiles, _ = await offline_manager.create_region("kotor", bbox, max_zoom=14)
        assert tiles == region.tile_count
        assert 0 < tiles <= settings.offline_max_region_tiles

    async def test_service_rejects_inverted_zoom(self):
        bbox = BoundingBox(min_lat=42.40, min_lng=18.70, max_lat=42.50, max_lng=18.80)
        with pytest.raises(ValueError):
            await offline_manager.create_region("x", bbox, min_zoom=10, max_zoom=2)

    async def test_cached_place_count_is_capped(self, monkeypatch):
        monkeypatch.setattr(settings, "offline_max_cached_places", 3)
        for i in range(3):
            await offline_manager.cache_place(_place(f"p{i}"))
        with pytest.raises(ValueError, match="limit"):
            await offline_manager.cache_place(_place("p3"))
        # re-caching an existing place is an update, not growth
        await offline_manager.cache_place(_place("p1"))

    def test_storage_limit_is_a_507_not_a_500(self, monkeypatch):
        monkeypatch.setattr(settings, "offline_storage_limit_mb", 0)
        monkeypatch.setattr("app.api.offline.discover", _fake_discovery)
        resp = client.post("/api/offline/places/cache", json={
            "bbox": {"min_lat": 42.4, "min_lng": 18.7, "max_lat": 42.5, "max_lng": 18.8},
        })
        assert resp.status_code == 507

    async def test_unsafe_place_id_is_skipped_not_fatal(self):
        good, bad = _place("good"), _place("bad")
        bad.id = "../../etc/passwd"
        count = await offline_manager.cache_places([bad, good])
        assert count == 1
        assert await offline_manager.get_cached_place("good") is not None


async def _fake_discovery(req):
    return DiscoverResponse(
        places=[_place("a1"), _place("outside", lat=50.0, lng=30.0)], total=2, has_more=False
    )


class TestBboxCaching:
    def test_bbox_caching_stores_discovered_places_inside_the_bbox(self, monkeypatch):
        """The endpoint imported a function that does not exist, swallowed the ImportError
        and therefore never cached anything."""
        monkeypatch.setattr("app.api.offline.discover", _fake_discovery)
        resp = client.post("/api/offline/places/cache", json={
            "bbox": {"min_lat": 42.4, "min_lng": 18.7, "max_lat": 42.5, "max_lng": 18.8},
        })
        assert resp.status_code == 200
        assert resp.json()["cached_count"] == 1
        assert client.get("/api/offline/places/a1").status_code == 200
        assert client.get("/api/offline/places/outside").status_code == 404
        assert client.get("/api/offline/places/a1").json()["has_llm_description"] is False


# ── wrong results ────────────────────────────────────────────────


class TestTileMath:
    def test_whole_world_at_zoom_0_is_one_tile(self):
        bbox = BoundingBox(min_lat=-85, min_lng=-180, max_lat=85, max_lng=180)
        assert _estimate_tile_count(bbox, 0, 0) == 1

    def test_polar_latitudes_do_not_crash(self):
        bbox = BoundingBox(min_lat=-90, min_lng=0, max_lat=90, max_lng=10)
        assert _estimate_tile_count(bbox, 0, 4) >= 1

    def test_bbox_exactly_on_a_tile_edge_is_not_over_counted(self):
        # z1: the north-west quadrant is exactly tile (0,0)
        bbox = BoundingBox(min_lat=0, min_lng=-180, max_lat=85.0511, max_lng=0)
        assert _estimate_tile_count(bbox, 1, 1) == 1


class TestNavigation:
    async def test_bearing_that_rounds_to_360_is_normalised(self):
        nav = await offline_manager.compute_navigation(
            Coordinates(lat=43.0, lng=18.0), Coordinates(lat=44.0, lng=17.99999)
        )
        assert 0 <= nav["bearing_deg"] < 360

    def test_nearly_north_target_does_not_500(self):
        _cache_sync(_place("north", lat=44.0, lng=17.99999))
        resp = client.post("/api/offline/navigate", json={
            "current_position": {"lat": 43.0, "lng": 18.0},
            "target_place_id": "north",
        })
        assert resp.status_code == 200
        assert 0 <= resp.json()["bearing_deg"] < 360


def _cache_sync(place: Place) -> None:
    import asyncio

    asyncio.run(offline_manager.cache_place(place))


class TestInputValidationIs422Not500:
    @pytest.mark.parametrize("qs", [
        "min_lat=200&min_lng=0&max_lat=1&max_lng=1",
        "min_lat=nan&min_lng=0&max_lat=1&max_lng=1",
    ])
    def test_list_places_bad_bbox(self, qs):
        assert client.get(f"/api/offline/places?{qs}").status_code == 422

    @pytest.mark.parametrize("qs", ["lat=200&lng=0", "lat=nan&lng=0", "lat=0&lng=-181"])
    def test_nearest_bad_coordinates(self, qs):
        assert client.post(f"/api/offline/navigate/nearest?{qs}").status_code == 422


# ── connectivity ─────────────────────────────────────────────────


class _Resp:
    status_code = 200


class _FakeClient:
    def __init__(self):
        self.calls = 0

    async def get(self, url, **kwargs):
        self.calls += 1
        return _Resp()


class TestConnectivity:
    async def test_reports_online_when_probe_succeeds(self, monkeypatch):
        fake = _FakeClient()
        monkeypatch.setattr("app.utils.http_client.get_http_client", lambda: fake)
        assert await offline_manager.check_connectivity() is True
        assert fake.calls == 1

    async def test_reports_offline_when_probe_fails(self):
        assert await offline_manager.check_connectivity() is False

    async def test_probe_result_is_cached_so_status_polling_does_not_hammer_upstream(
        self, monkeypatch
    ):
        fake = _FakeClient()
        monkeypatch.setattr("app.utils.http_client.get_http_client", lambda: fake)
        for _ in range(5):
            await offline_manager.check_connectivity()
        assert fake.calls == 1


class TestNavigationInput:
    @pytest.mark.parametrize("place_id", ["../x", "a/b", "a.b", "x" * 100])
    def test_unsafe_target_place_id_is_422_not_500(self, place_id):
        resp = client.post("/api/offline/navigate", json={
            "current_position": {"lat": 43.0, "lng": 18.0}, "target_place_id": place_id})
        assert resp.status_code == 422

    def test_unknown_but_valid_target_place_id_is_404(self):
        resp = client.post("/api/offline/navigate", json={
            "current_position": {"lat": 43.0, "lng": 18.0}, "target_place_id": "nope"})
        assert resp.status_code == 404


class TestCacheEndpointCost:
    def test_totals_come_from_directory_stats_not_from_parsing_every_cached_place(self, monkeypatch):
        """The endpoint parsed every cached place's JSON (up to 10k files) on each call just to
        add up file sizes, on the event loop."""
        for i in range(3):
            _cache_sync(_place(f"c{i}"))

        async def boom(*a, **k):
            raise AssertionError("list_cached_places parses every file")

        monkeypatch.setattr(offline_manager, "list_cached_places", boom)
        monkeypatch.setattr("app.api.offline.discover", _fake_discovery)
        resp = client.post("/api/offline/places/cache", json={
            "bbox": {"min_lat": 42.4, "min_lng": 18.7, "max_lat": 42.5, "max_lng": 18.8}})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_cached"] == 4  # 3 pre-cached + a1
        assert body["disk_usage_bytes"] > 0
