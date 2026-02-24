"""Tests for route export — GPX and KML generation (Story 4.4)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.models.route import (
    Route,
    RouteSegment,
    RouteWaypoint,
    TransportMode,
)
from app.services.route_export import export_gpx, export_kml


def _make_place(
    lat: float, lng: float, name: str, desc: str | None = None
) -> Place:
    return Place(
        id=f"test_{name}",
        source=PlaceSource.OSM,
        name=name,
        description=desc,
        categories=[PlaceCategory.LANDMARK],
        coordinates=Coordinates(lat=lat, lng=lng),
        confidence=0.8,
    )


@pytest.fixture
def sample_route() -> Route:
    origin = _make_place(42.45, 18.53, "Start", "Starting point")
    poi1 = _make_place(42.46, 18.54, "Old Bunker", "A WWII bunker")
    poi2 = _make_place(42.47, 18.55, "Hidden Cave", "A hidden cave")
    dest = _make_place(42.48, 18.56, "End", "Destination")

    waypoints = [
        RouteWaypoint(place=origin, order=0, is_origin=True),
        RouteWaypoint(place=poi1, order=1, detour_distance_m=200),
        RouteWaypoint(place=poi2, order=2, detour_distance_m=300),
        RouteWaypoint(place=dest, order=3, is_destination=True),
    ]

    segments = [
        RouteSegment(
            from_point=origin.coordinates,
            to_point=poi1.coordinates,
            distance_m=1500,
            duration_s=1071,
            transport_mode=TransportMode.WALKING,
        ),
        RouteSegment(
            from_point=poi1.coordinates,
            to_point=poi2.coordinates,
            distance_m=1500,
            duration_s=1071,
            transport_mode=TransportMode.WALKING,
        ),
        RouteSegment(
            from_point=poi2.coordinates,
            to_point=dest.coordinates,
            distance_m=1500,
            duration_s=1071,
            transport_mode=TransportMode.WALKING,
        ),
    ]

    return Route(
        id="test-route-001",
        waypoints=waypoints,
        segments=segments,
        total_distance_m=4500,
        total_duration_s=3213,
        transport_mode=TransportMode.WALKING,
        is_circular=False,
        places_count=2,
    )


class TestExportGPX:
    def test_valid_xml(self, sample_route):
        gpx_str = export_gpx(sample_route)
        assert gpx_str.startswith('<?xml version="1.0"')
        # Should parse without error
        root = ET.fromstring(gpx_str)
        assert root.tag.endswith("gpx")

    def test_contains_waypoints(self, sample_route):
        gpx_str = export_gpx(sample_route)
        root = ET.fromstring(gpx_str)
        ns = {"g": "http://www.topografix.com/GPX/1/1"}
        wpts = root.findall("g:wpt", ns)
        assert len(wpts) == 4  # origin + 2 POIs + destination

    def test_contains_track(self, sample_route):
        gpx_str = export_gpx(sample_route)
        root = ET.fromstring(gpx_str)
        ns = {"g": "http://www.topografix.com/GPX/1/1"}
        trkseg = root.find(".//g:trkseg", ns)
        assert trkseg is not None
        trkpts = trkseg.findall("g:trkpt", ns)
        assert len(trkpts) == 4

    def test_waypoint_coordinates(self, sample_route):
        gpx_str = export_gpx(sample_route)
        root = ET.fromstring(gpx_str)
        ns = {"g": "http://www.topografix.com/GPX/1/1"}
        wpts = root.findall("g:wpt", ns)
        assert wpts[0].get("lat") == "42.45"
        assert wpts[0].get("lon") == "18.53"

    def test_descriptions_included(self, sample_route):
        gpx_str = export_gpx(sample_route, include_descriptions=True)
        assert "A WWII bunker" in gpx_str

    def test_descriptions_excluded(self, sample_route):
        gpx_str = export_gpx(sample_route, include_descriptions=False)
        assert "A WWII bunker" not in gpx_str

    def test_metadata(self, sample_route):
        gpx_str = export_gpx(sample_route)
        assert "Terra Incognita" in gpx_str
        assert "walking" in gpx_str

    def test_gpx_version(self, sample_route):
        gpx_str = export_gpx(sample_route)
        assert 'version="1.1"' in gpx_str


class TestExportKML:
    def test_valid_xml(self, sample_route):
        kml_str = export_kml(sample_route)
        assert kml_str.startswith('<?xml version="1.0"')
        root = ET.fromstring(kml_str)
        assert root.tag.endswith("kml")

    def test_contains_placemarks(self, sample_route):
        kml_str = export_kml(sample_route)
        root = ET.fromstring(kml_str)
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        placemarks = root.findall(".//k:Placemark", ns)
        # 4 waypoints + 1 route line = 5
        assert len(placemarks) == 5

    def test_contains_linestring(self, sample_route):
        kml_str = export_kml(sample_route)
        root = ET.fromstring(kml_str)
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        linestring = root.find(".//k:LineString", ns)
        assert linestring is not None

    def test_styles_present(self, sample_route):
        kml_str = export_kml(sample_route)
        assert "routeStyle" in kml_str
        assert "startStyle" in kml_str
        assert "endStyle" in kml_str
        assert "waypointStyle" in kml_str

    def test_descriptions_included(self, sample_route):
        kml_str = export_kml(sample_route, include_descriptions=True)
        assert "A WWII bunker" in kml_str

    def test_descriptions_excluded(self, sample_route):
        kml_str = export_kml(sample_route, include_descriptions=False)
        assert "A WWII bunker" not in kml_str

    def test_kml_coordinates_format(self, sample_route):
        kml_str = export_kml(sample_route)
        # KML uses lng,lat,alt format
        assert "18.53,42.45,0" in kml_str
