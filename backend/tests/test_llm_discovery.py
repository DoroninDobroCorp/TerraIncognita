"""Tests for Story 2.1 + 2.5 — Natural Language Discovery & Smart Query Understanding."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.chat import ChatMessage, ChatRequest, ParsedIntent
from app.models.place import PlaceCategory
from app.services.llm_discovery import (
    _detect_language,
    _fallback_intent,
    _fallback_response,
    process_chat,
)


# ── Fallback Intent Tests ──────────────────────────────────────


class TestFallbackIntent:
    """Test rule-based fallback intent parsing."""

    def test_russian_abandoned_query(self):
        intent = _fallback_intent("хочу заброшку у воды")
        assert PlaceCategory.ABANDONED in intent.categories

    def test_english_underground_query(self):
        intent = _fallback_intent("show me underground tunnels")
        assert PlaceCategory.UNDERGROUND in intent.categories

    def test_stalker_mood_query(self):
        intent = _fallback_intent("что-то как в Сталкере")
        cats = intent.categories
        assert PlaceCategory.ABANDONED in cats
        assert PlaceCategory.INDUSTRIAL in cats

    def test_romantic_query(self):
        intent = _fallback_intent("romantic hidden spot")
        assert PlaceCategory.VIEWPOINT in intent.categories or \
               PlaceCategory.NATURE_HIDDEN in intent.categories

    def test_cave_query(self):
        intent = _fallback_intent("найди пещеры рядом")
        assert PlaceCategory.CAVE in intent.categories

    def test_military_query(self):
        intent = _fallback_intent("old military bunker")
        assert PlaceCategory.MILITARY in intent.categories

    def test_church_query_ru(self):
        intent = _fallback_intent("покажи старые церкви")
        assert PlaceCategory.RELIGIOUS in intent.categories

    def test_museum_query(self):
        intent = _fallback_intent("interesting museums nearby")
        assert PlaceCategory.MUSEUM in intent.categories

    def test_graffiti_query(self):
        intent = _fallback_intent("street art and graffiti spots")
        assert PlaceCategory.STREET_ART in intent.categories

    def test_vague_query_returns_broad_categories(self):
        intent = _fallback_intent("show me something cool")
        assert len(intent.categories) >= 3

    def test_water_query(self):
        intent = _fallback_intent("водопады и озёра")
        assert PlaceCategory.WATER in intent.categories

    def test_combined_mood_query(self):
        intent = _fallback_intent("жуткое заброшенное подземелье")
        cats = intent.categories
        assert PlaceCategory.ABANDONED in cats
        assert PlaceCategory.UNDERGROUND in cats


# ── Language Detection Tests ──────────────────────────────────


class TestLanguageDetection:
    def test_russian_text(self):
        intent = ParsedIntent(original_query="покажи заброшки")
        assert _detect_language(intent) == "ru"

    def test_english_text(self):
        intent = ParsedIntent(original_query="show me abandoned places")
        assert _detect_language(intent) == "en"

    def test_mixed_text_mostly_cyrillic(self):
        intent = ParsedIntent(original_query="хочу stalker vibes рядом")
        assert _detect_language(intent) == "ru"


# ── Fallback Response Tests ──────────────────────────────────


class TestFallbackResponse:
    def test_no_places_russian(self):
        resp = _fallback_response([], "ru")
        assert "не удалось" in resp.lower() or "найти" in resp.lower()

    def test_no_places_english(self):
        resp = _fallback_response([], "en")
        assert "no places" in resp.lower()

    def test_with_places_russian(self):
        from app.models.place import Coordinates, Place, PlaceSource
        places = [Place(
            id="test1", source=PlaceSource.OSM,
            coordinates=Coordinates(lat=42.0, lng=19.0),
            name="Test Place",
        )]
        resp = _fallback_response(places, "ru")
        assert "1" in resp

    def test_with_places_english(self):
        from app.models.place import Coordinates, Place, PlaceSource
        places = [Place(
            id="test1", source=PlaceSource.OSM,
            coordinates=Coordinates(lat=42.0, lng=19.0),
            name="Test Place",
        )]
        resp = _fallback_response(places, "en")
        assert "1" in resp


# ── Process Chat Integration Tests ───────────────────────────


class TestProcessChat:
    @pytest.fixture
    def chat_request(self):
        return ChatRequest(
            message="покажи заброшки у воды",
            lat=42.65,
            lng=18.09,
            radius_km=5.0,
        )

    @pytest.mark.asyncio
    async def test_process_chat_with_llm_failure_uses_fallback(self, chat_request):
        """When LLM is unavailable, should still return results via fallback."""
        with patch(
            "app.services.llm_discovery.chat_completion",
            new_callable=AsyncMock,
            side_effect=ValueError("API key not set"),
        ), patch(
            "app.services.llm_discovery.discover",
            new_callable=AsyncMock,
        ) as mock_discover:
            from app.models.place import Coordinates, DiscoverResponse, Place, PlaceSource
            mock_discover.return_value = DiscoverResponse(
                places=[Place(
                    id="p1", source=PlaceSource.OSM, name="Old Factory",
                    coordinates=Coordinates(lat=42.65, lng=18.09),
                    categories=[PlaceCategory.ABANDONED],
                )],
                total=1, has_more=False,
            )

            result = await process_chat(chat_request)
            assert result.conversation_id
            assert len(result.places) == 1
            assert result.message  # Fallback message generated

    @pytest.mark.asyncio
    async def test_process_chat_with_llm_success(self, chat_request):
        """When LLM works, should return parsed intent and generated response."""
        intent_json = '{"categories": ["abandoned"], "mood": ["creepy"], "terrain": ["urban"], "distance_preference": "nearby", "keywords": ["water"]}'

        with patch(
            "app.services.llm_discovery.chat_completion",
            new_callable=AsyncMock,
            side_effect=[intent_json, "Found an amazing abandoned factory by the river!"],
        ), patch(
            "app.services.llm_discovery.discover",
            new_callable=AsyncMock,
        ) as mock_discover:
            from app.models.place import Coordinates, DiscoverResponse, Place, PlaceSource
            mock_discover.return_value = DiscoverResponse(
                places=[Place(
                    id="p1", source=PlaceSource.OSM, name="Old Factory",
                    coordinates=Coordinates(lat=42.65, lng=18.09),
                    categories=[PlaceCategory.ABANDONED],
                    distance_m=500.0,
                )],
                total=1, has_more=False,
            )

            result = await process_chat(chat_request)
            assert result.parsed_intent is not None
            assert PlaceCategory.ABANDONED in result.parsed_intent.categories
            assert "factory" in result.message.lower() or result.message

    @pytest.mark.asyncio
    async def test_process_chat_with_history(self):
        """Chat with conversation history should include context."""
        req = ChatRequest(
            message="а что-нибудь поближе?",
            lat=42.65,
            lng=18.09,
            radius_km=3.0,
            history=[
                ChatMessage(role="user", content="покажи заброшки"),
                ChatMessage(role="assistant", content="Нашёл 5 заброшенных мест!"),
            ],
            conversation_id="conv-123",
        )

        with patch(
            "app.services.llm_discovery.chat_completion",
            new_callable=AsyncMock,
            side_effect=[
                '{"categories": ["abandoned"], "mood": [], "terrain": [], "distance_preference": "nearby", "keywords": []}',
                "Here are closer abandoned places!",
            ],
        ), patch(
            "app.services.llm_discovery.discover",
            new_callable=AsyncMock,
        ) as mock_discover:
            from app.models.place import Coordinates, DiscoverResponse, Place, PlaceSource
            mock_discover.return_value = DiscoverResponse(
                places=[], total=0, has_more=False,
            )

            result = await process_chat(req)
            assert result.conversation_id == "conv-123"

    @pytest.mark.asyncio
    async def test_empty_results_response(self):
        """Should handle empty results gracefully."""
        req = ChatRequest(
            message="find nuclear reactor",
            lat=0.0, lng=0.0, radius_km=1.0,
        )

        with patch(
            "app.services.llm_discovery.chat_completion",
            new_callable=AsyncMock,
            side_effect=[
                '{"categories": ["military"], "mood": [], "terrain": [], "keywords": ["nuclear"]}',
                "No places found nearby.",
            ],
        ), patch(
            "app.services.llm_discovery.discover",
            new_callable=AsyncMock,
        ) as mock_discover:
            from app.models.place import DiscoverResponse
            mock_discover.return_value = DiscoverResponse(
                places=[], total=0, has_more=False,
            )

            result = await process_chat(req)
            assert result.places == []
            assert result.message
