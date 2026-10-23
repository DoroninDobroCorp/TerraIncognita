"""Tests for geo utilities."""

from __future__ import annotations

import pytest

from app.utils.geo import bounding_box, haversine_distance_m


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_distance_m(42.0, 18.0, 42.0, 18.0) == 0.0

    def test_known_distance(self):
        # Roughly 111km per degree of latitude
        dist = haversine_distance_m(42.0, 18.0, 43.0, 18.0)
        assert 110_000 < dist < 112_000

    def test_symmetric(self):
        d1 = haversine_distance_m(42.0, 18.0, 43.0, 19.0)
        d2 = haversine_distance_m(43.0, 19.0, 42.0, 18.0)
        assert d1 == pytest.approx(d2)

    def test_short_distance(self):
        # ~50 meters
        dist = haversine_distance_m(42.450000, 18.530000, 42.450450, 18.530000)
        assert 40 < dist < 60


class TestBoundingBox:
    def test_bbox_dimensions(self):
        south, west, north, east = bounding_box(42.0, 18.0, 5.0)
        assert south < 42.0 < north
        assert west < 18.0 < east

    def test_bbox_radius(self):
        south, west, north, east = bounding_box(42.0, 18.0, 10.0)
        # North-South span should be roughly 2 * radius_km / 111.32 degrees
        lat_span = north - south
        expected_span = 2 * 10.0 / 111.32
        assert lat_span == pytest.approx(expected_span, rel=0.01)

    def test_equator(self):
        south, west, north, east = bounding_box(0.0, 0.0, 1.0)
        assert south < 0 < north
        assert west < 0 < east


class TestBoundingBoxStaysOnTheGlobe:
    """Wikidata answered HTTP 500 for any box beyond +-180 / +-90, so a search near the antimeridian or a pole failed every time."""

    @pytest.mark.parametrize(("lat", "lng", "radius"), [
        (-16.85, 179.98, 10.0), (65.6, -179.99, 50.0), (-90.0, 0.0, 5.0), (89.99, 0.0, 50.0), (90.0, 180.0, 50.0), (0.0, -180.0, 50.0),
    ])
    def test_the_box_is_valid(self, lat, lng, radius):
        south, west, north, east = bounding_box(lat, lng, radius)
        assert -90 <= south < north <= 90
        assert -180 <= west < east <= 180

    def test_a_box_that_fits_is_not_changed(self):
        south, west, north, east = bounding_box(42.0, 18.0, 10.0)
        assert (north - south) == pytest.approx(2 * 10.0 / 111.19, rel=0.01)
        assert west < 18.0 < east

    def test_the_wikidata_query_is_valid_near_the_antimeridian(self):
        from app.sources import wikidata as wd

        south, west, north, east = bounding_box(-16.85, 179.98, 10.0)
        query = wd._SPARQL_TEMPLATE.format(
            south=south, west=west, north=north, east=east, type_values="wd:Q1",
            common_values="wd:Q2", limit=10,
        )
        assert "Point(180.0" in query or "Point(180 " in query  # the east corner is the 180th meridian, not 180.07
