"""Models for POST /api/place/ask."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_QUESTION_CHARS = 500
MAX_DESCRIPTION_CHARS = 2000
MAX_NAME_CHARS = 200
MAX_CATEGORIES = 10
MAX_CATEGORY_CHARS = 40


class PlaceAskPlace(BaseModel):
    """The one place the question is about, as the client knows it.

    Free-text fields are truncated rather than rejected: they are copied from place cards,
    and a long scraped description should not make the question fail.
    """

    name: str | None = None
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    categories: list[str] = Field(default_factory=list)
    description: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _cut_name(cls, v: Any) -> Any:
        return v[:MAX_NAME_CHARS] if isinstance(v, str) else v

    @field_validator("description", mode="before")
    @classmethod
    def _cut_description(cls, v: Any) -> Any:
        return v[:MAX_DESCRIPTION_CHARS] if isinstance(v, str) else v

    @field_validator("categories", mode="before")
    @classmethod
    def _cut_categories(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        return [c[:MAX_CATEGORY_CHARS] for c in v if isinstance(c, str)][:MAX_CATEGORIES]


class PlaceAskRequest(BaseModel):
    place: PlaceAskPlace
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    language: str = Field("auto", pattern=r"^(auto|[a-z]{2})$")

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be blank")
        return v


class PlaceAskResponse(BaseModel):
    answer: str
