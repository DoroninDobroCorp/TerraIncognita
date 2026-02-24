"""Route models for the Smart Route Builder (Epic 4)."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.place import Coordinates, Place, PlaceCategory


class TransportMode(enum.StrEnum):
    """Supported transport modes for routing."""

    WALKING = "walking"
    CYCLING = "cycling"
    DRIVING = "driving"


class RouteSegment(BaseModel):
    """A segment between two consecutive waypoints."""

    from_point: Coordinates
    to_point: Coordinates
    distance_m: float = Field(..., ge=0, description="Segment distance in meters")
    duration_s: float = Field(..., ge=0, description="Estimated travel time in seconds")
    transport_mode: TransportMode = TransportMode.WALKING
    elevation_gain_m: float = Field(
        0.0, ge=0,
        description="Elevation gain (requires external elevation API; 0 when unavailable)",
    )
    geometry: list[Coordinates] = Field(
        default_factory=list, description="Polyline coordinates for rendering"
    )


class RouteWaypoint(BaseModel):
    """A waypoint in a route, tied to a discovered Place."""

    place: Place
    order: int = Field(..., ge=0, description="Position in route sequence")
    detour_distance_m: float = Field(
        0.0, ge=0, description="Extra distance added by visiting this waypoint"
    )
    detour_duration_s: float = Field(
        0.0, ge=0, description="Extra time added by visiting this waypoint"
    )
    is_origin: bool = False
    is_destination: bool = False


class Route(BaseModel):
    """Complete route through multiple points of interest."""

    id: str
    waypoints: list[RouteWaypoint]
    segments: list[RouteSegment] = Field(default_factory=list)
    total_distance_m: float = Field(0.0, ge=0)
    total_duration_s: float = Field(0.0, ge=0)
    total_elevation_gain_m: float = Field(
        0.0, ge=0,
        description="Total elevation gain (requires external elevation API; 0 when unavailable)",
    )
    transport_mode: TransportMode = TransportMode.WALKING
    is_circular: bool = False
    places_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- API Request/Response models ---


class RouteRequest(BaseModel):
    """POST /api/route — build a route through interesting places."""

    origin: Coordinates = Field(..., description="Starting point")
    destination: Coordinates | None = Field(
        None, description="End point (null for circular route)"
    )
    transport_mode: TransportMode = TransportMode.WALKING
    max_duration_hours: float = Field(
        4.0, gt=0, le=24.0, description="Maximum route duration in hours"
    )
    categories: list[PlaceCategory] = Field(
        default_factory=list, description="Filter POIs by category"
    )
    corridor_width_km: float = Field(
        1.0, gt=0, le=10.0, description="Search corridor width around route (km)"
    )
    max_waypoints: int = Field(
        10, ge=1, le=50, description="Maximum number of POI stops"
    )
    exclude_visited: list[str] = Field(
        default_factory=list, max_length=1000, description="IDs of already visited places"
    )
    optimize: bool = Field(True, description="Apply TSP optimization to waypoint order")
    surprise_mode: bool = Field(
        False, description="Reveal places only as user approaches"
    )


class ExploreRouteRequest(BaseModel):
    """POST /api/route/explore — generate a circular exploration route."""

    origin: Coordinates = Field(..., description="Starting point (also return point)")
    transport_mode: TransportMode = TransportMode.WALKING
    max_duration_hours: float = Field(
        4.0, gt=0, le=24.0, description="Time budget in hours"
    )
    categories: list[PlaceCategory] = Field(
        default_factory=list, description="Filter POIs by category"
    )
    radius_km: float = Field(
        5.0, gt=0, le=50.0, description="Search radius from origin"
    )
    max_waypoints: int = Field(
        8, ge=1, le=50, description="Number of POI stops"
    )
    exclude_visited: list[str] = Field(
        default_factory=list, max_length=1000
    )
    surprise_mode: bool = False


class RouteResponse(BaseModel):
    """Route response with full details."""

    route: Route
    discovered_places: int = Field(
        0, description="Total places found in corridor before filtering"
    )
    summary: str = Field(
        "", description="Human-readable route summary"
    )
    navigation_links: dict[str, str] = Field(
        default_factory=dict,
        description="Deep links for external navigation apps",
    )


class RouteExportRequest(BaseModel):
    """POST /api/route/export — export route to GPX/KML."""

    route: Route
    format: str = Field("gpx", pattern=r"^(gpx|kml)$")
    include_descriptions: bool = True


class RouteExportResponse(BaseModel):
    """Export response with file content."""

    content: str = Field(..., description="GPX or KML file content")
    format: str
    filename: str


class RouteReorderRequest(BaseModel):
    """POST /api/route/reorder — manually reorder waypoints."""

    route: Route
    waypoint_order: list[int] = Field(
        ..., description="New order of waypoint indices (excluding origin/destination)"
    )
