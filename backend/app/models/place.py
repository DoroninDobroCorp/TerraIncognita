"""Core domain models for the Discovery Engine."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config import settings


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


class WikipediaInfo(BaseModel):
    """Wikipedia article summary attached to a place (filled by the enrich endpoint)."""

    title: str
    extract: str
    url: str
    lang: str


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
    # Rule-based "how unusual is this" signal (see services/scoring.py): 0 = mainstream, 1 = gem.
    unusual_score: float = Field(0.0, ge=0.0, le=1.0)
    unusual_reasons: list[str] = Field(default_factory=list)
    # Not filled by /api/discover; returned by POST /api/place/enrich and may be stored by clients.
    wikipedia: WikipediaInfo | None = None
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
    radius_km: float = Field(default_factory=lambda: settings.default_radius_km, gt=0, le=settings.max_radius_km)
    categories: list[PlaceCategory] = Field(default_factory=list)
    exclude_visited: list[str] = Field(default_factory=list, max_length=1000)
    limit: int = Field(50, ge=1, le=200)
    sort_by: str = Field("confidence", pattern=r"^(confidence|distance|name|unusual)$")
    cursor: str | None = None


class DiscoverResponse(BaseModel):
    """POST /api/discover response body."""

    places: list[Place]
    total: int
    has_more: bool
    cursor: str | None = None
    deep_research_status: str = "idle"  # "idle" | "pending" | "cached"
    deep_research_message: str | None = None
    # Per-source outcome: "ok" | "empty" | "error" | "timeout". A failing source never fails the request.
    source_status: dict[str, str] = Field(default_factory=dict)


# --- Enrichment (POST /api/place/enrich) ---


class EnrichRequest(BaseModel):
    """POST /api/place/enrich request body."""

    name: str | None = Field(None, max_length=200)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    wikidata_id: str | None = Field(None, pattern=r"^Q[1-9]\d{0,11}$")
    lang: str = Field("en", pattern=r"^[a-z]{2,3}$")

    @field_validator("name")
    @classmethod
    def _blank_name_is_none(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    @field_validator("wikidata_id", mode="before")
    @classmethod
    def _normalise_wikidata_id(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip().upper()
            return v or None
        return v

    @field_validator("lang", mode="before")
    @classmethod
    def _normalise_lang(cls, v: object) -> object:
        """Accept what browsers send ("en-US", "RU", "pt_BR"): keep the language subtag."""
        if isinstance(v, str) and len(v) <= 16:
            return v.strip().lower().replace("_", "-").split("-")[0]
        return v


class EnrichedPhoto(BaseModel):
    """A freely-licensed photo with the attribution its licence requires."""

    url: str
    thumb_url: str
    title: str = ""
    author: str = ""
    license: str = ""
    source_url: str = ""


class EnrichResponse(BaseModel):
    """POST /api/place/enrich response body."""

    wikipedia: WikipediaInfo | None = None
    photos: list[EnrichedPhoto] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)  # providers that contributed
