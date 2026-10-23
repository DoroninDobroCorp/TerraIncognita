"""Geo-spatial utility helpers."""

from __future__ import annotations

import math


def haversine_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return the great-circle distance in meters between two WGS-84 points."""
    r = 6_371_000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    """Return (south, west, north, east) bounding box for the given centre + radius.

    The box is clamped to the globe (|lat| <= 90, |lng| <= 180): upstream services reject anything outside it.
    A search within ``radius_km`` of the antimeridian therefore sees only the side it is on.
    """
    r = 6_371.0  # km
    d_lat = math.degrees(radius_km / r)
    d_lng = min(math.degrees(radius_km / (r * max(math.cos(math.radians(lat)), 1e-6))), 180.0)
    return (max(lat - d_lat, -90.0), max(lng - d_lng, -180.0), min(lat + d_lat, 90.0), min(lng + d_lng, 180.0))
