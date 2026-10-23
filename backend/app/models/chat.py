"""Chat and LLM Intelligence Layer models."""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from app.config import settings
from app.models.place import Place, PlaceCategory

MAX_FREE_FORM_JSON = 8_000  # serialised size of client-supplied metadata / context dicts


def _bounded_json(value: dict[str, Any]) -> dict[str, Any]:
    """Client-supplied dicts go (partly) into LLM prompts: bound their size."""
    try:
        size = len(json.dumps(value, default=str, ensure_ascii=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("must be JSON-serialisable") from exc
    if size > MAX_FREE_FORM_JSON:
        raise ValueError(f"too large ({size} > {MAX_FREE_FORM_JSON} characters when serialised)")
    return value


BoundedDict = Annotated[dict[str, Any], AfterValidator(_bounded_json)]
PlaceId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Tag = Annotated[str, StringConstraints(max_length=100)]

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
    content: str = Field(..., max_length=2000)


class ChatRequest(BaseModel):
    """POST /api/chat request body."""

    message: str = Field(..., min_length=1, max_length=2000)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(min(5.0, settings.max_radius_km), gt=0, le=settings.max_radius_km)
    conversation_id: str | None = Field(None, max_length=100)
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

    place_id: PlaceId
    place_name: str | None = Field(None, max_length=300)
    place_categories: list[PlaceCategory] = Field(default_factory=list, max_length=30)
    place_tags: list[Tag] = Field(default_factory=list, max_length=50)
    place_metadata: BoundedDict = Field(default_factory=dict)
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
    radius_km: float = Field(min(5.0, settings.max_radius_km), gt=0, le=settings.max_radius_km)
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    context: BoundedDict = Field(default_factory=dict)  # time_of_day, weather, etc.
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

    place_id: PlaceId
    place_name: str | None = Field(None, max_length=300)
    place_categories: list[PlaceCategory] = Field(default_factory=list, max_length=30)
    place_tags: list[Tag] = Field(default_factory=list, max_length=50)
    place_metadata: BoundedDict = Field(default_factory=dict)
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
