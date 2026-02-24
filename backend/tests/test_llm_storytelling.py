"""Tests for Story 2.4 — Storytelling (text generation)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.llm_storytelling import (
    _fallback_route_story,
    _fallback_story,
    generate_place_story,
    generate_route_story,
)


def _make_place(pid: str, name: str, categories: list[PlaceCategory]) -> Place:
    return Place(
        id=pid, source=PlaceSource.OSM, name=name,
        coordinates=Coordinates(lat=42.65, lng=18.09),
        categories=categories,
        tags=["historic", "ruins"],
        metadata={"osm_tags": {"historic": "castle"}},
    )


# ── Fallback Tests ───────────────────────────────────────────


class TestFallbackStory:
    def test_fallback_ru(self):
        place = _make_place("p1", "Старая Крепость", [PlaceCategory.RUINS])
        result = _fallback_story(place, "ru")
        assert result["place_id"] == "p1"
        assert "Старая Крепость" in result["story"]
        assert result["ai_generated"] is False

    def test_fallback_en(self):
        place = _make_place("p1", "Old Fort", [PlaceCategory.RUINS])
        result = _fallback_story(place, "en")
        assert "Old Fort" in result["story"]
        assert result["ai_generated"] is False

    def test_fallback_unnamed(self):
        place = _make_place("p1", None, [PlaceCategory.ABANDONED])
        result = _fallback_story(place, "en")
        assert result["story"]  # Should produce something


class TestFallbackRouteStory:
    def test_fallback_route_ru(self):
        places = [
            _make_place("p1", "Крепость", [PlaceCategory.RUINS]),
            _make_place("p2", "Пещера", [PlaceCategory.CAVE]),
        ]
        result = _fallback_route_story(places, "ru")
        assert len(result["place_ids"]) == 2
        assert "Крепость" in result["story"]

    def test_fallback_route_en(self):
        places = [
            _make_place("p1", "Fort", [PlaceCategory.RUINS]),
            _make_place("p2", "Cave", [PlaceCategory.CAVE]),
        ]
        result = _fallback_route_story(places, "en")
        assert "2 stops" in result["story"]


# ── LLM Story Generation (mocked) ───────────────────────────


class TestGeneratePlaceStory:
    @pytest.mark.asyncio
    async def test_successful_generation(self):
        place = _make_place("p1", "Abandoned Factory", [PlaceCategory.ABANDONED])
        mock_story = (
            "The old factory stands like a silent sentinel against the evening sky. "
            "Built in 1920, it once hummed with the energy of a thousand workers..."
        )

        with patch(
            "app.services.llm_storytelling.cached_completion",
            new_callable=AsyncMock,
            return_value=mock_story,
        ):
            result = await generate_place_story(place, "en")
            assert result["place_id"] == "p1"
            assert "factory" in result["story"].lower()
            assert result["ai_generated"] is True

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback(self):
        place = _make_place("p1", "Old Bridge", [PlaceCategory.ARCHITECTURE])

        with patch(
            "app.services.llm_storytelling.cached_completion",
            new_callable=AsyncMock,
            side_effect=Exception("LLM down"),
        ):
            result = await generate_place_story(place, "en")
            assert result["ai_generated"] is False
            assert result["story"]

    @pytest.mark.asyncio
    async def test_russian_story(self):
        place = _make_place("p1", "Бункер", [PlaceCategory.MILITARY])
        mock_story = "Бетонные стены этого бункера хранят тайны холодной войны..."

        with patch(
            "app.services.llm_storytelling.cached_completion",
            new_callable=AsyncMock,
            return_value=mock_story,
        ):
            result = await generate_place_story(place, "ru")
            assert result["language"] == "ru"
            assert "бункер" in result["story"].lower()


class TestGenerateRouteStory:
    @pytest.mark.asyncio
    async def test_successful_route_story(self):
        places = [
            _make_place("p1", "Castle Ruins", [PlaceCategory.RUINS]),
            _make_place("p2", "Hidden Cave", [PlaceCategory.CAVE]),
            _make_place("p3", "Old Lighthouse", [PlaceCategory.ARCHITECTURE]),
        ]
        mock_story = (
            "Your adventure begins at the ancient Castle Ruins, where crumbling "
            "walls whisper tales of battles long past. As you leave the castle "
            "and head toward the Hidden Cave..."
        )

        with patch(
            "app.services.llm_storytelling.cached_completion",
            new_callable=AsyncMock,
            return_value=mock_story,
        ):
            result = await generate_route_story(places, "en")
            assert len(result["place_ids"]) == 3
            assert result["ai_generated"] is True
            assert "castle" in result["story"].lower()

    @pytest.mark.asyncio
    async def test_empty_places_returns_empty(self):
        result = await generate_route_story([], "en")
        assert result["story"] == ""
        assert result["ai_generated"] is False

    @pytest.mark.asyncio
    async def test_route_llm_failure(self):
        places = [
            _make_place("p1", "Fort", [PlaceCategory.RUINS]),
            _make_place("p2", "Cave", [PlaceCategory.CAVE]),
        ]

        with patch(
            "app.services.llm_storytelling.cached_completion",
            new_callable=AsyncMock,
            side_effect=Exception("error"),
        ):
            result = await generate_route_story(places, "en")
            assert result["ai_generated"] is False
            assert result["place_ids"] == ["p1", "p2"]
