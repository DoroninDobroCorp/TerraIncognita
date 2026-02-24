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
