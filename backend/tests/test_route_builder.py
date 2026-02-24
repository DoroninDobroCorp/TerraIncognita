"""Tests for route builder service and API endpoints (Epic 4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.place import Coordinates, DiscoverResponse, Place, PlaceCategory, PlaceSource
from app.models.route import (
    ExploreRouteRequest,
    Route,
    RouteExportRequest,
    RouteRequest,
    RouteResponse,
    TransportMode,
)
from app.services.route_builder import (
    _apply_surprise_mode,
    _build_route_object,
    _generate_nav_links,
    _generate_qr_data_uri,
    build_explore_route,
    build_route,
)


def _make_places(n: int, base_lat: float = 42.45, base_lng: float = 18.53) -> list[Place]:
    return [
        Place(
            id=f"mock_{i}",
            source=PlaceSource.OSM,
            name=f"Place {i}",
            categories=[PlaceCategory.LANDMARK],
            coordinates=Coordinates(lat=base_lat + i * 0.005, lng=base_lng + i * 0.005),
            confidence=max(0.1, 0.9 - i * 0.05),
        )
        for i in range(n)
    ]


def _mock_discover_response(places: list[Place]) -> DiscoverResponse:
    return DiscoverResponse(
        places=places,
        total=len(places),
        has_more=False,
        cursor=None,
    )


class TestBuildRouteObject:
    def test_basic_route(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        dest = Coordinates(lat=42.50, lng=18.58)
        places = _make_places(3)
        route = _build_route_object(origin, dest, places, TransportMode.WALKING, False)

        assert isinstance(route, Route)
        assert route.places_count == 3
        # origin + 3 POIs + destination = 5
        assert len(route.waypoints) == 5
        assert len(route.segments) == 4
        assert route.waypoints[0].is_origin
        assert route.waypoints[-1].is_destination
        assert route.total_distance_m > 0
        assert route.total_duration_s > 0

    def test_circular_route(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        places = _make_places(2)
        route = _build_route_object(origin, None, places, TransportMode.WALKING, True)

        assert route.is_circular
        assert route.waypoints[0].is_origin
        assert route.waypoints[-1].is_destination
        assert route.waypoints[-1].place.name == "Return to Start"
        # Origin coords == return coords
        assert route.waypoints[-1].place.coordinates == origin

    def test_empty_places(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        route = _build_route_object(origin, None, [], TransportMode.WALKING, True)
        assert route.places_count == 0
        # origin + return = 2
        assert len(route.waypoints) == 2

    def test_transport_modes(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        places = _make_places(2)

        walk = _build_route_object(origin, None, places, TransportMode.WALKING, False)
        cycle = _build_route_object(origin, None, places, TransportMode.CYCLING, False)
        drive = _build_route_object(origin, None, places, TransportMode.DRIVING, False)

        assert walk.transport_mode == TransportMode.WALKING
        assert cycle.transport_mode == TransportMode.CYCLING
        assert drive.transport_mode == TransportMode.DRIVING
        # Same distance, different duration
        assert walk.total_distance_m == cycle.total_distance_m
        assert walk.total_duration_s > cycle.total_duration_s > drive.total_duration_s


class TestBuildRoute:
    @pytest.mark.asyncio
    async def test_point_to_point(self):
        places = _make_places(5)
        mock_response = _mock_discover_response(places)

        with patch("app.services.route_builder.discover", new_callable=AsyncMock) as mock_disc:
            mock_disc.return_value = mock_response
            req = RouteRequest(
                origin=Coordinates(lat=42.45, lng=18.53),
                destination=Coordinates(lat=42.50, lng=18.58),
                transport_mode=TransportMode.WALKING,
                max_duration_hours=4.0,
                corridor_width_km=2.0,
                max_waypoints=10,
            )
            result = await build_route(req)

        assert isinstance(result, RouteResponse)
        assert result.route.total_distance_m > 0
        assert result.discovered_places == 5

    @pytest.mark.asyncio
    async def test_circular_route(self):
        places = _make_places(3)
        mock_response = _mock_discover_response(places)

        with patch("app.services.route_builder.discover", new_callable=AsyncMock) as mock_disc:
            mock_disc.return_value = mock_response
            req = RouteRequest(
                origin=Coordinates(lat=42.45, lng=18.53),
                destination=None,
                transport_mode=TransportMode.WALKING,
                max_duration_hours=4.0,
            )
            result = await build_route(req)

        assert result.route.is_circular

    @pytest.mark.asyncio
    async def test_max_waypoints_limit(self):
        places = _make_places(20)
        mock_response = _mock_discover_response(places)

        with patch("app.services.route_builder.discover", new_callable=AsyncMock) as mock_disc:
            mock_disc.return_value = mock_response
            req = RouteRequest(
                origin=Coordinates(lat=42.45, lng=18.53),
                destination=None,
                max_waypoints=3,
                max_duration_hours=24.0,
            )
            result = await build_route(req)

        # POI waypoints should be <= max_waypoints
        poi_waypoints = [
            w for w in result.route.waypoints
            if not w.is_origin and not w.is_destination
        ]
        assert len(poi_waypoints) <= 3


class TestBuildExploreRoute:
    @pytest.mark.asyncio
    async def test_explore_basic(self):
        places = _make_places(4)
        mock_response = _mock_discover_response(places)

        with patch("app.services.route_builder.discover", new_callable=AsyncMock) as mock_disc:
            mock_disc.return_value = mock_response
            req = ExploreRouteRequest(
                origin=Coordinates(lat=42.45, lng=18.53),
                transport_mode=TransportMode.WALKING,
                max_duration_hours=4.0,
                radius_km=5.0,
            )
            result = await build_explore_route(req)

        assert isinstance(result, RouteResponse)
        assert result.route.is_circular
        assert result.discovered_places == 4

    @pytest.mark.asyncio
    async def test_explore_with_categories(self):
        places = _make_places(3)
        mock_response = _mock_discover_response(places)

        with patch("app.services.route_builder.discover", new_callable=AsyncMock) as mock_disc:
            mock_disc.return_value = mock_response
            req = ExploreRouteRequest(
                origin=Coordinates(lat=42.45, lng=18.53),
                categories=[PlaceCategory.ABANDONED, PlaceCategory.RUINS],
                max_duration_hours=2.0,
            )
            await build_explore_route(req)

        # Verify categories were passed to discover
        call_args = mock_disc.call_args[0][0]
        assert PlaceCategory.ABANDONED in call_args.categories

    @pytest.mark.asyncio
    async def test_explore_no_places(self):
        mock_response = _mock_discover_response([])

        with patch("app.services.route_builder.discover", new_callable=AsyncMock) as mock_disc:
            mock_disc.return_value = mock_response
            req = ExploreRouteRequest(
                origin=Coordinates(lat=42.45, lng=18.53),
            )
            result = await build_explore_route(req)

        assert result.route.places_count == 0


class TestRouteModels:
    def test_route_request_validation(self):
        req = RouteRequest(
            origin=Coordinates(lat=42.45, lng=18.53),
            transport_mode=TransportMode.CYCLING,
            max_duration_hours=2.0,
        )
        assert req.destination is None
        assert req.optimize is True

    def test_route_request_bounds(self):
        with pytest.raises(Exception):
            RouteRequest(
                origin=Coordinates(lat=91.0, lng=18.53),  # invalid lat
            )

    def test_explore_request_defaults(self):
        req = ExploreRouteRequest(
            origin=Coordinates(lat=42.45, lng=18.53),
        )
        assert req.max_duration_hours == 4.0
        assert req.radius_km == 5.0
        assert req.max_waypoints == 8
        assert req.surprise_mode is False

    def test_transport_modes(self):
        assert TransportMode.WALKING.value == "walking"
        assert TransportMode.CYCLING.value == "cycling"
        assert TransportMode.DRIVING.value == "driving"

    def test_export_request_format_validation(self):
        route = Route(
            id="test",
            waypoints=[],
            transport_mode=TransportMode.WALKING,
        )
        req = RouteExportRequest(route=route, format="gpx")
        assert req.format == "gpx"

        req = RouteExportRequest(route=route, format="kml")
        assert req.format == "kml"

        with pytest.raises(Exception):
            RouteExportRequest(route=route, format="pdf")


class TestSurpriseMode:
    def test_apply_surprise_mode_hides_details(self):
        places = _make_places(3)
        hidden = _apply_surprise_mode(places)
        assert len(hidden) == 3
        for i, h in enumerate(hidden):
            assert h.name == f"Mystery Stop #{i + 1}"
            assert "Surprise" in h.description
            assert h.photos == []
            assert h.metadata["_surprise_original_name"] == places[i].name

    def test_apply_surprise_mode_empty(self):
        assert _apply_surprise_mode([]) == []


class TestQRCodeGeneration:
    def test_qr_data_uri_contains_url(self):
        url = "https://www.google.com/maps/dir/42.45,18.53/42.50,18.58"
        qr = _generate_qr_data_uri(url)
        assert "qrserver.com" in qr
        assert "42.45" in qr

    def test_nav_links_include_qr(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        places = _make_places(2)
        route = _build_route_object(origin, None, places, TransportMode.WALKING, True)
        links = _generate_nav_links(route)
        assert "qr_data" in links
        assert "google_maps" in links
