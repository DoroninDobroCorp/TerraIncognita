"""Core domain models for the Discovery Engine."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class PlaceCategory(str, enum.Enum):
    """Categories covering both unusual exploration targets and classic landmarks."""

    # Unusual / urban exploration
    ABANDONED = "abandoned"
    UNDERGROUND = "underground"
    INDUSTRIAL = "industrial"
    RUINS = "ruins"
    MILITARY = "military"
    NATURE_HIDDEN = "nature_hidden"
    VIEWPOINT = "viewpoint"
    STREET_ART = "street_art"
    TRANSPORT = "transport"
    WATER = "water"
    CAVE = "cave"

    # Classic landmarks
    RELIGIOUS = "religious"
    LANDMARK = "landmark"
    MUSEUM = "museum"
    ARCHITECTURE = "architecture"
    MONUMENT = "monument"
    PARK = "park"

    # Deep research categories
    CULINARY = "culinary"
    UNDERWATER = "underwater"

    # Notable / outstanding (only truly exceptional places)
    RESTAURANT_NOTABLE = "restaurant_notable"
    HOTEL_NOTABLE = "hotel_notable"


class PlaceSource(str, enum.Enum):
    OSM = "osm"
    ATLAS_OBSCURA = "atlas"
    WIKIDATA = "wiki"
    FLICKR = "flickr"
    LLM = "llm"
    COMMUNITY = "community"
    DEEP_RESEARCH = "deep_research"


class Coordinates(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class Place(BaseModel):
    """Unified place representation aggregated from multiple sources."""

    id: str
    source: PlaceSource
    sources: list[PlaceSource] = Field(default_factory=list)
    name: str | None = None
    description: str | None = None
    categories: list[PlaceCategory] = Field(default_factory=list)
    coordinates: Coordinates
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    category_confidence: dict[str, float] = Field(default_factory=dict)
    distance_m: float | None = None
    tags: list[str] = Field(default_factory=list)
    photos: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_unusual(self) -> bool:
        """Whether this place is in the 'unusual/exploration' category group."""
        unusual = {
            PlaceCategory.ABANDONED,
            PlaceCategory.UNDERGROUND,
            PlaceCategory.INDUSTRIAL,
            PlaceCategory.RUINS,
            PlaceCategory.MILITARY,
            PlaceCategory.NATURE_HIDDEN,
            PlaceCategory.STREET_ART,
            PlaceCategory.CAVE,
        }
        return bool(set(self.categories) & unusual)


# --- API request/response models ---


class DiscoverRequest(BaseModel):
    """POST /api/discover request body."""

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(5.0, gt=0, le=50.0)
    categories: list[PlaceCategory] = Field(default_factory=list)
    exclude_visited: list[str] = Field(default_factory=list, max_length=1000)
    limit: int = Field(50, ge=1, le=200)
    sort_by: str = Field("confidence", pattern=r"^(confidence|distance|name)$")
    cursor: str | None = None


class DiscoverResponse(BaseModel):
    """POST /api/discover response body."""

    places: list[Place]
    total: int
    has_more: bool
    cursor: str | None = None
    deep_research_status: str = "idle"  # "idle" | "pending" | "cached"
    deep_research_message: str | None = None
