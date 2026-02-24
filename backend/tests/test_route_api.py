"""API-level tests for route endpoints using FastAPI TestClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.place import Coordinates, DiscoverResponse, Place, PlaceCategory, PlaceSource

client = TestClient(app)


def _make_places(n: int) -> list[Place]:
    return [
        Place(
            id=f"api_{i}",
            source=PlaceSource.OSM,
            name=f"API Place {i}",
            categories=[PlaceCategory.LANDMARK],
            coordinates=Coordinates(lat=42.45 + i * 0.005, lng=18.53 + i * 0.005),
            confidence=0.8,
        )
        for i in range(n)
    ]


def _mock_discover(places):
    return DiscoverResponse(
        places=places, total=len(places), has_more=False, cursor=None,
    )


class TestRouteAPI:
    def test_create_route(self):
        places = _make_places(3)
        with patch(
            "app.services.route_builder.discover",
            new_callable=AsyncMock,
            return_value=_mock_discover(places),
        ):
            resp = client.post("/api/route", json={
                "origin": {"lat": 42.45, "lng": 18.53},
                "destination": {"lat": 42.50, "lng": 18.58},
                "transport_mode": "walking",
                "max_duration_hours": 4.0,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert "route" in data
        assert "summary" in data
        assert "navigation_links" in data
        assert data["route"]["total_distance_m"] > 0
        assert len(data["summary"]) > 0

    def test_create_route_invalid_coords(self):
        resp = client.post("/api/route", json={
            "origin": {"lat": 91.0, "lng": 18.53},
        })
        assert resp.status_code == 422

    def test_create_route_invalid_transport(self):
        resp = client.post("/api/route", json={
            "origin": {"lat": 42.45, "lng": 18.53},
            "transport_mode": "helicopter",
        })
        assert resp.status_code == 422


class TestExploreRouteAPI:
    def test_explore_route(self):
        places = _make_places(4)
        with patch(
            "app.services.route_builder.discover",
            new_callable=AsyncMock,
            return_value=_mock_discover(places),
        ):
            resp = client.post("/api/route/explore", json={
                "origin": {"lat": 42.45, "lng": 18.53},
                "transport_mode": "cycling",
                "max_duration_hours": 2.0,
                "radius_km": 5.0,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["route"]["is_circular"] is True
        assert "cycling" in data["summary"].lower()

    def test_explore_route_with_categories(self):
        with patch(
            "app.services.route_builder.discover",
            new_callable=AsyncMock,
            return_value=_mock_discover([]),
        ):
            resp = client.post("/api/route/explore", json={
                "origin": {"lat": 42.45, "lng": 18.53},
                "categories": ["abandoned", "ruins"],
            })

        assert resp.status_code == 200


class TestExportAPI:
    def _make_route_payload(self):
        return {
            "id": "test-export",
            "waypoints": [
                {
                    "place": {
                        "id": "p1", "source": "osm", "name": "Start",
                        "coordinates": {"lat": 42.45, "lng": 18.53},
                    },
                    "order": 0, "is_origin": True,
                },
                {
                    "place": {
                        "id": "p2", "source": "osm", "name": "POI",
                        "coordinates": {"lat": 42.46, "lng": 18.54},
                        "categories": ["landmark"],
                    },
                    "order": 1,
                },
                {
                    "place": {
                        "id": "p3", "source": "osm", "name": "End",
                        "coordinates": {"lat": 42.47, "lng": 18.55},
                    },
                    "order": 2, "is_destination": True,
                },
            ],
            "total_distance_m": 3000,
            "total_duration_s": 2142,
            "transport_mode": "walking",
            "places_count": 1,
        }

    def test_export_gpx(self):
        resp = client.post("/api/route/export", json={
            "route": self._make_route_payload(),
            "format": "gpx",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "gpx"
        assert "<?xml" in data["content"]
        assert data["filename"].endswith(".gpx")

    def test_export_kml(self):
        resp = client.post("/api/route/export", json={
            "route": self._make_route_payload(),
            "format": "kml",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "kml"
        assert "kml" in data["content"].lower()

    def test_export_invalid_format(self):
        resp = client.post("/api/route/export", json={
            "route": self._make_route_payload(),
            "format": "pdf",
        })
        assert resp.status_code == 422

    def test_export_empty_route(self):
        resp = client.post("/api/route/export", json={
            "route": {
                "id": "empty",
                "waypoints": [],
                "transport_mode": "walking",
            },
            "format": "gpx",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "<?xml" in data["content"]


class TestReorderAPI:
    def test_reorder_waypoints(self):
        route_payload = {
            "id": "reorder-test",
            "waypoints": [
                {
                    "place": {
                        "id": "origin", "source": "osm", "name": "Start",
                        "coordinates": {"lat": 42.45, "lng": 18.53},
                    },
                    "order": 0, "is_origin": True,
                },
                {
                    "place": {
                        "id": "poi1", "source": "osm", "name": "First",
                        "coordinates": {"lat": 42.46, "lng": 18.54},
                    },
                    "order": 1,
                },
                {
                    "place": {
                        "id": "poi2", "source": "osm", "name": "Second",
                        "coordinates": {"lat": 42.47, "lng": 18.55},
                    },
                    "order": 2,
                },
                {
                    "place": {
                        "id": "dest", "source": "osm", "name": "End",
                        "coordinates": {"lat": 42.48, "lng": 18.56},
                    },
                    "order": 3, "is_destination": True,
                },
            ],
            "total_distance_m": 4000,
            "total_duration_s": 2857,
            "transport_mode": "walking",
            "places_count": 2,
        }

        resp = client.post("/api/route/reorder", json={
            "route": route_payload,
            "waypoint_order": [1, 0],  # swap POIs
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "route" in data
        assert "summary" in data

    def test_reorder_invalid_order(self):
        route_payload = {
            "id": "reorder-test",
            "waypoints": [
                {
                    "place": {
                        "id": "origin", "source": "osm", "name": "Start",
                        "coordinates": {"lat": 42.45, "lng": 18.53},
                    },
                    "order": 0, "is_origin": True,
                },
                {
                    "place": {
                        "id": "poi1", "source": "osm", "name": "POI",
                        "coordinates": {"lat": 42.46, "lng": 18.54},
                    },
                    "order": 1,
                },
                {
                    "place": {
                        "id": "dest", "source": "osm", "name": "End",
                        "coordinates": {"lat": 42.47, "lng": 18.55},
                    },
                    "order": 2, "is_destination": True,
                },
            ],
            "transport_mode": "walking",
            "places_count": 1,
        }

        resp = client.post("/api/route/reorder", json={
            "route": route_payload,
            "waypoint_order": [0, 1],  # invalid: only 1 POI
        })
        assert resp.status_code == 400


class TestNavigationLinks:
    def test_google_maps_link(self):
        places = _make_places(2)
        with patch(
            "app.services.route_builder.discover",
            new_callable=AsyncMock,
            return_value=_mock_discover(places),
        ):
            resp = client.post("/api/route", json={
                "origin": {"lat": 42.45, "lng": 18.53},
                "destination": {"lat": 42.50, "lng": 18.58},
                "max_duration_hours": 8.0,
            })

        assert resp.status_code == 200
        data = resp.json()
        links = data["navigation_links"]
        assert "google_maps" in links
        assert "google.com/maps" in links["google_maps"]
        assert "osmand" in links
        assert "geo" in links
