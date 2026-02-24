"""Chat and LLM Intelligence Layer models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.place import Place, PlaceCategory


# ── Story 2.1 + 2.5: Natural Language Discovery ─────────────────


class ParsedIntent(BaseModel):
    """Structured intent extracted from natural language query."""

    categories: list[PlaceCategory] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    terrain: list[str] = Field(default_factory=list)
    distance_preference: str | None = None  # "nearby", "walkable", "any"
    time_of_day: str | None = None
    keywords: list[str] = Field(default_factory=list)
    original_query: str = ""


class ChatMessage(BaseModel):
    """Single message in a conversation."""

    role: str = Field(..., pattern=r"^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    """POST /api/chat request body."""

    message: str = Field(..., min_length=1, max_length=2000)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(5.0, gt=0, le=50.0)
    conversation_id: str | None = None
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    language: str = Field("auto", pattern=r"^(auto|ru|en|[a-z]{2})$")


class ChatResponse(BaseModel):
    """POST /api/chat response body."""

    message: str
    places: list[Place] = Field(default_factory=list)
    parsed_intent: ParsedIntent | None = None
    conversation_id: str
    language: str = "auto"
    suggested_questions: list[str] = Field(default_factory=list)


# ── Story 2.2: Place Description Generation ─────────────────────


class DescriptionRequest(BaseModel):
    """POST /api/describe request body."""

    place_id: str
    place_name: str | None = None
    place_categories: list[PlaceCategory] = Field(default_factory=list)
    place_tags: list[str] = Field(default_factory=list)
    place_metadata: dict[str, Any] = Field(default_factory=dict)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    language: str = Field("auto", pattern=r"^(auto|ru|en|[a-z]{2})$")


class DescriptionResponse(BaseModel):
    """POST /api/describe response body."""

    place_id: str
    description: str
    practical_info: str | None = None
    ai_generated: bool = True
    cached: bool = False
    language: str = "auto"


# ── Story 2.3: Contextual Recommendations ───────────────────────


class UserPreferences(BaseModel):
    """User preferences for contextual recommendations."""

    favorite_categories: list[PlaceCategory] = Field(default_factory=list)
    visited_place_ids: list[str] = Field(default_factory=list, max_length=1000)
    liked_place_ids: list[str] = Field(default_factory=list, max_length=500)
    disliked_place_ids: list[str] = Field(default_factory=list, max_length=500)


class RecommendationRequest(BaseModel):
    """POST /api/recommend request body."""

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(5.0, gt=0, le=50.0)
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    context: dict[str, Any] = Field(default_factory=dict)  # time_of_day, weather, etc.
    limit: int = Field(10, ge=1, le=50)
    language: str = Field("auto", pattern=r"^(auto|ru|en|[a-z]{2})$")


class RecommendedPlace(BaseModel):
    """A place with a recommendation reason."""

    place: Place
    reason: str
    relevance_score: float = Field(0.5, ge=0.0, le=1.0)


class RecommendationResponse(BaseModel):
    """POST /api/recommend response body."""

    recommendations: list[RecommendedPlace]
    total: int
    strategy: str  # "personalized", "popular", "diverse", "cold_start"
    language: str = "auto"


# ── Story 2.4: Storytelling Mode (text only, TTS deferred to V2) ─


class StoryRequest(BaseModel):
    """POST /api/story request body."""

    place_id: str
    place_name: str | None = None
    place_categories: list[PlaceCategory] = Field(default_factory=list)
    place_tags: list[str] = Field(default_factory=list)
    place_metadata: dict[str, Any] = Field(default_factory=dict)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    language: str = Field("ru", pattern=r"^(auto|ru|en|[a-z]{2})$")


class StoryResponse(BaseModel):
    """POST /api/story response body."""

    story: str
    place_id: str
    place_name: str | None = None
    ai_generated: bool = True
    language: str = "ru"


class RouteStoryRequest(BaseModel):
    """POST /api/story/route request body."""

    places: list[StoryRequest] = Field(..., min_length=2, max_length=20)
    language: str = Field("ru", pattern=r"^(auto|ru|en|[a-z]{2})$")


class RouteStoryResponse(BaseModel):
    """POST /api/story/route response body."""

    story: str
    place_ids: list[str]
    ai_generated: bool = True
    language: str = "ru"
