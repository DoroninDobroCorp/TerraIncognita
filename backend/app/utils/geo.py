"""Geo-spatial utility helpers."""

from __future__ import annotations

import math


def haversine_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return the great-circle distance in meters between two WGS-84 points."""
    R = 6_371_000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    """Return (south, west, north, east) bounding box for the given centre + radius."""
    R = 6_371.0  # km
    d_lat = math.degrees(radius_km / R)
    d_lng = math.degrees(radius_km / (R * math.cos(math.radians(lat))))
    return (lat - d_lat, lng - d_lng, lat + d_lat, lng + d_lng)
