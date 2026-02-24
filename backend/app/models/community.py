"""Community domain models (Epic 8) — User-generated content and social features."""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.models.place import Coordinates, PlaceCategory


# ── Story 8.1: User-Submitted Places ────────────────────────────


class ModerationStatus(str, enum.Enum):
    """Status of a community-submitted place in the moderation pipeline."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class CommunityPlace(BaseModel):
    """A place submitted by a community member."""

    id: str
    submitted_by: str
    name: str
    description: str = ""
    categories: list[PlaceCategory] = Field(default_factory=list)
    coordinates: Coordinates
    photos: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=20)
    moderation_status: ModerationStatus = ModerationStatus.PENDING
    confirmations: list[str] = Field(default_factory=list)
    rejections: list[str] = Field(default_factory=list)
    osm_suggested: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContributorKarma(BaseModel):
    """Karma tracking for a community contributor."""

    user_id: str
    karma: int = 0
    places_submitted: int = 0
    places_confirmed: int = 0
    reviews_written: int = 0
    helpful_votes_received: int = 0
    routes_published: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Story 8.2: Reviews & Tips ───────────────────────────────────


class ReviewType(str, enum.Enum):
    """Type of user content about a place."""

    REVIEW = "review"
    TIP = "tip"


class Review(BaseModel):
    """A review or tip about a place."""

    id: str
    place_id: str
    author_id: str
    review_type: ReviewType = ReviewType.REVIEW
    text: str
    photos: list[str] = Field(default_factory=list, max_length=10)
    visit_date: str | None = None  # "January 2026" style recency
    upvotes: list[str] = Field(default_factory=list)
    downvotes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def score(self) -> int:
        return len(self.upvotes) - len(self.downvotes)


# ── Story 8.3: Social Routes ────────────────────────────────────


class PublishedRoute(BaseModel):
    """A route published by a community member."""

    id: str
    author_id: str
    title: str
    description: str = ""
    region: str = ""
    waypoint_place_ids: list[str] = Field(default_factory=list)
    distance_km: float = 0.0
    duration_hours: float = 0.0
    tags: list[str] = Field(default_factory=list, max_length=20)
    ratings: list[RouteRating] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def average_rating(self) -> float:
        if not self.ratings:
            return 0.0
        return round(sum(r.score for r in self.ratings) / len(self.ratings), 1)


class RouteRating(BaseModel):
    """A rating for a published route."""

    user_id: str
    score: int = Field(..., ge=1, le=5)
    comment: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# Fix forward reference
PublishedRoute.model_rebuild()


class ExplorerFollow(BaseModel):
    """A follow relationship between explorers."""

    follower_id: str
    following_id: str
    consent_given: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── API Request/Response Models ──────────────────────────────────


# Story 8.1 API models

class SubmitPlaceRequest(BaseModel):
    """POST /api/community/places — submit a new place."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    categories: list[PlaceCategory] = Field(default_factory=list)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    photos: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=20)
    author_id: str = Field(..., min_length=1, max_length=100)


class ConfirmPlaceRequest(BaseModel):
    """POST /api/community/places/{id}/confirm — confirm or reject a place."""

    user_id: str = Field(..., min_length=1, max_length=100)
    confirm: bool = True


class CommunityPlaceListResponse(BaseModel):
    """GET /api/community/places response."""

    places: list[CommunityPlace]
    total: int


class KarmaResponse(BaseModel):
    """GET /api/community/karma/{user_id} response."""

    karma: ContributorKarma


# Story 8.2 API models

class CreateReviewRequest(BaseModel):
    """POST /api/community/places/{id}/reviews — write a review or tip."""

    author_id: str = Field(..., min_length=1, max_length=100)
    review_type: ReviewType = ReviewType.REVIEW
    text: str = Field(..., min_length=1, max_length=5000)
    photos: list[str] = Field(default_factory=list, max_length=10)
    visit_date: str | None = Field(None, max_length=50)


class VoteRequest(BaseModel):
    """POST /api/community/reviews/{id}/vote — upvote or downvote."""

    user_id: str = Field(..., min_length=1, max_length=100)
    upvote: bool = True


class ReviewListResponse(BaseModel):
    """GET /api/community/places/{id}/reviews response."""

    reviews: list[Review]
    total: int


# Story 8.3 API models

class PublishRouteRequest(BaseModel):
    """POST /api/community/routes — publish a route."""

    author_id: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=5000)
    region: str = Field("", max_length=200)
    waypoint_place_ids: list[str] = Field(..., min_length=2)
    distance_km: float = Field(0.0, ge=0)
    duration_hours: float = Field(0.0, ge=0)
    tags: list[str] = Field(default_factory=list, max_length=20)


class RateRouteRequest(BaseModel):
    """POST /api/community/routes/{id}/rate — rate a route."""

    user_id: str = Field(..., min_length=1, max_length=100)
    score: int = Field(..., ge=1, le=5)
    comment: str = Field("", max_length=2000)


class FollowRequest(BaseModel):
    """POST /api/community/follow — follow an explorer."""

    follower_id: str = Field(..., min_length=1, max_length=100)
    following_id: str = Field(..., min_length=1, max_length=100)


class ConsentRequest(BaseModel):
    """POST /api/community/follow/consent — grant consent for follower."""

    user_id: str = Field(..., min_length=1, max_length=100)
    follower_id: str = Field(..., min_length=1, max_length=100)
    consent: bool = True


class PublishedRouteListResponse(BaseModel):
    """GET /api/community/routes response."""

    routes: list[PublishedRoute]
    total: int


class FollowListResponse(BaseModel):
    """GET /api/community/followers or following response."""

    follows: list[ExplorerFollow]
    total: int


# Update/Delete models

class UpdatePlaceRequest(BaseModel):
    """PATCH /api/community/places/{id} — update a submitted place."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    categories: list[PlaceCategory] | None = None
    photos: list[str] | None = Field(None, max_length=10)
    tags: list[str] | None = Field(None, max_length=20)


class UpdateReviewRequest(BaseModel):
    """PATCH /api/community/reviews/{id} — update a review."""

    text: str | None = Field(None, min_length=1, max_length=5000)
    photos: list[str] | None = Field(None, max_length=10)
    visit_date: str | None = Field(None, max_length=50)


class PlaceSummary(BaseModel):
    """Summary of community activity for a place."""

    place_id: str
    review_count: int = 0
    tip_count: int = 0
    average_score: float = 0.0
    latest_visit_date: str | None = None


class ReportOutdatedRequest(BaseModel):
    """POST /api/community/reviews/{id}/report-outdated — flag info as outdated."""

    user_id: str = Field(..., min_length=1, max_length=100)
    reason: str = Field("", max_length=500)
