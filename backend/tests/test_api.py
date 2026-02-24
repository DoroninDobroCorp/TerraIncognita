"""Tests for Story 1.6 — Search API endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.place import (
    Coordinates,
    DiscoverResponse,
    Place,
    PlaceCategory,
    PlaceSource,
)

client = TestClient(app)


def _sample_response() -> DiscoverResponse:
    return DiscoverResponse(
        places=[
            Place(
                id="osm_node_1",
                source=PlaceSource.OSM,
                sources=[PlaceSource.OSM],
                name="Test Bunker",
                categories=[PlaceCategory.MILITARY],
                coordinates=Coordinates(lat=42.45, lng=18.53),
                confidence=0.8,
            ),
        ],
        total=1,
        has_more=False,
        cursor=None,
    )


class TestHealthEndpoint:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestDiscoverEndpoint:
    @patch("app.api.discover.discover", new_callable=AsyncMock)
    def test_basic_discover(self, mock_discover):
        mock_discover.return_value = _sample_response()
        resp = client.post("/api/discover", json={
            "lat": 42.45,
            "lng": 18.53,
            "radius_km": 5.0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["places"][0]["name"] == "Test Bunker"
        assert data["has_more"] is False

    @patch("app.api.discover.discover", new_callable=AsyncMock)
    def test_discover_with_filters(self, mock_discover):
        mock_discover.return_value = _sample_response()
        resp = client.post("/api/discover", json={
            "lat": 42.45,
            "lng": 18.53,
            "radius_km": 10.0,
            "categories": ["military", "abandoned"],
            "exclude_visited": ["osm_node_999"],
            "limit": 20,
            "sort_by": "distance",
        })
        assert resp.status_code == 200
        # Verify the request was forwarded correctly
        req = mock_discover.call_args[0][0]
        assert req.radius_km == 10.0
        assert PlaceCategory.MILITARY in req.categories
        assert req.limit == 20
        assert req.sort_by == "distance"

    def test_validation_lat_out_of_range(self):
        resp = client.post("/api/discover", json={
            "lat": 100.0,
            "lng": 18.53,
        })
        assert resp.status_code == 422

    def test_validation_radius_too_large(self):
        resp = client.post("/api/discover", json={
            "lat": 42.0,
            "lng": 18.0,
            "radius_km": 100.0,
        })
        assert resp.status_code == 422

    def test_validation_invalid_sort(self):
        resp = client.post("/api/discover", json={
            "lat": 42.0,
            "lng": 18.0,
            "sort_by": "invalid",
        })
        assert resp.status_code == 422

    def test_validation_limit_bounds(self):
        resp = client.post("/api/discover", json={
            "lat": 42.0,
            "lng": 18.0,
            "limit": 0,
        })
        assert resp.status_code == 422
        resp2 = client.post("/api/discover", json={
            "lat": 42.0,
            "lng": 18.0,
            "limit": 999,
        })
        assert resp2.status_code == 422

    @patch("app.api.discover.discover", new_callable=AsyncMock)
    def test_discover_with_cursor(self, mock_discover):
        mock_discover.return_value = DiscoverResponse(
            places=[], total=0, has_more=False, cursor=None,
        )
        resp = client.post("/api/discover", json={
            "lat": 42.0,
            "lng": 18.0,
            "cursor": "abc123",
        })
        assert resp.status_code == 200

    @patch("app.api.discover.discover", new_callable=AsyncMock)
    def test_discover_error_returns_500(self, mock_discover):
        mock_discover.side_effect = RuntimeError("boom")
        resp = client.post("/api/discover", json={
            "lat": 42.0,
            "lng": 18.0,
        })
        assert resp.status_code == 500
        assert "failed" in resp.json()["detail"].lower()
