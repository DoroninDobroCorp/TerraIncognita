"""Explorer Journal domain models (Epic 5)."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.place import Coordinates, PlaceCategory


# ── Story 5.1: Visit Tracking ───────────────────────────────────


class VisitStatus(str, enum.Enum):
    """Status of a place from the user's perspective."""

    VISITED = "visited"
    WANT_TO_VISIT = "want_to_visit"
    SKIP = "skip"


class Visit(BaseModel):
    """A recorded visit to a place."""

    id: str
    place_id: str
    place_name: str | None = None
    status: VisitStatus = VisitStatus.VISITED
    coordinates: Coordinates
    visited_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_minutes: float | None = None
    auto_detected: bool = False
    trip_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Story 5.2: Place Notes & Rating ─────────────────────────────


class PlaceRating(BaseModel):
    """Multi-dimensional rating of a place."""

    atmosphere: int = Field(0, ge=0, le=5)
    accessibility: int = Field(0, ge=0, le=5)
    photogenic: int = Field(0, ge=0, le=5)
    uniqueness: int = Field(0, ge=0, le=5)

    @property
    def average(self) -> float:
        scores = [self.atmosphere, self.accessibility, self.photogenic, self.uniqueness]
        non_zero = [s for s in scores if s > 0]
        return round(sum(non_zero) / len(non_zero), 1) if non_zero else 0.0


class PlaceNote(BaseModel):
    """Personal note attached to a visited place."""

    id: str
    visit_id: str
    place_id: str
    text: str = ""
    rating: PlaceRating = Field(default_factory=PlaceRating)
    tags: list[str] = Field(default_factory=list)
    photos: list[str] = Field(default_factory=list)
    voice_transcript: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Story 5.3: Exploration Statistics ────────────────────────────


class CategoryStat(BaseModel):
    """Visit count for a single category."""

    category: str
    count: int = 0


class HeatmapPoint(BaseModel):
    """A point on the visited-places heatmap."""

    lat: float
    lng: float
    intensity: float = Field(1.0, ge=0.0, le=1.0)


class StreakInfo(BaseModel):
    """Streak tracking: consecutive days with a new place visit."""

    current_streak: int = 0
    longest_streak: int = 0
    last_visit_date: str | None = None


class SurprisePlace(BaseModel):
    """A place where actual rating significantly differed from expected."""

    place_id: str
    place_name: str | None = None
    expected_score: float = 0.0
    actual_score: float = 0.0
    delta: float = 0.0


class ExplorationStats(BaseModel):
    """Aggregated exploration statistics."""

    total_visited: int = 0
    total_want_to_visit: int = 0
    total_skipped: int = 0
    by_category: list[CategoryStat] = Field(default_factory=list)
    total_distance_km: float = 0.0
    total_hours: float = 0.0
    streak: StreakInfo = Field(default_factory=StreakInfo)
    surprises: list[SurprisePlace] = Field(default_factory=list)


# ── Story 5.4: Trip Organization ────────────────────────────────


class Trip(BaseModel):
    """A trip grouping visits together."""

    id: str
    name: str
    region: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    visit_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TripSummary(BaseModel):
    """Summary of a trip for display."""

    trip: Trip
    total_places: int = 0
    categories: list[CategoryStat] = Field(default_factory=list)
    total_distance_km: float = 0.0
    total_hours: float = 0.0
    best_rated_places: list[str] = Field(default_factory=list)
    heatmap: list[HeatmapPoint] = Field(default_factory=list)


# ── API Request/Response Models ──────────────────────────────────


class CheckInRequest(BaseModel):
    """POST /api/visits — check in at a place."""

    place_id: str
    place_name: str | None = None
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    status: VisitStatus = VisitStatus.VISITED
    duration_minutes: float | None = None
    trip_id: str | None = None


class ProximityCheckRequest(BaseModel):
    """POST /api/visits/proximity — auto-detect if user is near a known place."""

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    radius_m: float = Field(100.0, gt=0, le=500)


class ProximityCheckResponse(BaseModel):
    """Response for proximity check."""

    nearby_place_id: str | None = None
    nearby_place_name: str | None = None
    distance_m: float | None = None
    already_visited: bool = False


class VisitUpdateRequest(BaseModel):
    """PATCH /api/visits/{id} — update visit details."""

    status: VisitStatus | None = None
    duration_minutes: float | None = None
    trip_id: str | None = None


class VisitListResponse(BaseModel):
    """GET /api/visits response."""

    visits: list[Visit]
    total: int


class NoteCreateRequest(BaseModel):
    """POST /api/visits/{id}/notes — add a note."""

    text: str = ""
    rating: PlaceRating = Field(default_factory=PlaceRating)
    tags: list[str] = Field(default_factory=list, max_length=50)
    photos: list[str] = Field(default_factory=list, max_length=20)
    voice_transcript: str | None = None


class NoteUpdateRequest(BaseModel):
    """PATCH /api/notes/{id} — update a note."""

    text: str | None = None
    rating: PlaceRating | None = None
    tags: list[str] | None = None
    photos: list[str] | None = None
    voice_transcript: str | None = None


class NoteListResponse(BaseModel):
    """GET /api/visits/{id}/notes response."""

    notes: list[PlaceNote]
    total: int


class StatsResponse(BaseModel):
    """GET /api/stats response."""

    stats: ExplorationStats


class HeatmapResponse(BaseModel):
    """GET /api/stats/heatmap response."""

    points: list[HeatmapPoint]
    total: int


class TripCreateRequest(BaseModel):
    """POST /api/trips — create a trip."""

    name: str = Field(..., min_length=1, max_length=200)
    region: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


class TripUpdateRequest(BaseModel):
    """PATCH /api/trips/{id} — update a trip."""

    name: str | None = Field(None, min_length=1, max_length=200)
    region: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


class TripListResponse(BaseModel):
    """GET /api/trips response."""

    trips: list[Trip]
    total: int


class TripSummaryResponse(BaseModel):
    """GET /api/trips/{id}/summary response."""

    summary: TripSummary


class TripExportRequest(BaseModel):
    """POST /api/trips/{id}/export — export trip."""

    format: str = Field("markdown", pattern=r"^(markdown|json|html)$")


class TripExportResponse(BaseModel):
    """Export response."""

    content: str
    format: str
    filename: str


class TripAutoGroupRequest(BaseModel):
    """POST /api/trips/{id}/auto-group — auto-group visits into this trip."""

    start_date: datetime
    end_date: datetime
    region_lat: float | None = Field(None, ge=-90, le=90)
    region_lng: float | None = Field(None, ge=-180, le=180)
    region_radius_km: float | None = Field(None, gt=0, le=500)


class DwellCheckRequest(BaseModel):
    """POST /api/visits/dwell-check — check if user dwelled long enough to suggest check-in."""

    place_id: str
    place_name: str | None = None
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    dwell_minutes: float = Field(..., gt=0)


class DwellCheckResponse(BaseModel):
    """Response for dwell check."""

    should_prompt: bool = False
    place_id: str | None = None
    place_name: str | None = None
    dwell_minutes: float = 0.0
    already_visited: bool = False
