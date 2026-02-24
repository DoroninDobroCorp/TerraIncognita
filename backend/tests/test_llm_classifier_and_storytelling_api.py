"""Tests for Story 1.5 LLM classifier fallback and Story 2.4 Storytelling API."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.classifier import classify_place, classify_place_llm

client = TestClient(app)


# ── LLM Classifier Tests ────────────────────────────────────


class TestLLMClassifier:
    def test_unclassifiable_place_marked_for_llm(self):
        """Place with no matching keywords should be marked for LLM classification."""
        place = Place(
            id="p1", source=PlaceSource.OSM,
            name="Zxy Object",  # No matching keywords
            coordinates=Coordinates(lat=42.0, lng=18.0),
        )
        classify_place(place)
        assert place.metadata.get("_needs_llm_classification") is True
        assert place.categories == [PlaceCategory.LANDMARK]
        assert place.category_confidence[PlaceCategory.LANDMARK.value] == 0.3

    def test_classifiable_place_not_marked_for_llm(self):
        """Place with matching keywords should NOT be marked for LLM."""
        place = Place(
            id="p2", source=PlaceSource.OSM,
            name="Abandoned Factory",
            coordinates=Coordinates(lat=42.0, lng=18.0),
        )
        classify_place(place)
        assert place.metadata.get("_needs_llm_classification") is None
        assert PlaceCategory.ABANDONED in place.categories

    @pytest.mark.asyncio
    async def test_llm_classify_success(self):
        """LLM classifier should update categories on success."""
        place = Place(
            id="p3", source=PlaceSource.OSM,
            name="Strange Object",
            coordinates=Coordinates(lat=42.0, lng=18.0),
            categories=[PlaceCategory.LANDMARK],
            category_confidence={PlaceCategory.LANDMARK.value: 0.3},
            metadata={"_needs_llm_classification": True},
        )

        with patch(
            "app.services.llm_client.chat_completion",
            new_callable=AsyncMock,
            return_value='{"categories": ["abandoned", "industrial"], "confidence": 0.75}',
        ):
            result = await classify_place_llm(place)
            assert PlaceCategory.ABANDONED in result.categories
            assert PlaceCategory.INDUSTRIAL in result.categories
            assert result.metadata.get("_needs_llm_classification") is None

    @pytest.mark.asyncio
    async def test_llm_classify_failure_keeps_default(self):
        """LLM classifier failure should keep rule-based default."""
        place = Place(
            id="p4", source=PlaceSource.OSM,
            name="Unknown",
            coordinates=Coordinates(lat=42.0, lng=18.0),
            categories=[PlaceCategory.LANDMARK],
            category_confidence={PlaceCategory.LANDMARK.value: 0.3},
            metadata={"_needs_llm_classification": True},
        )

        with patch(
            "app.services.llm_client.chat_completion",
            new_callable=AsyncMock,
            side_effect=Exception("LLM down"),
        ):
            result = await classify_place_llm(place)
            assert PlaceCategory.LANDMARK in result.categories  # Kept default

    @pytest.mark.asyncio
    async def test_llm_classify_invalid_json(self):
        """LLM returning invalid JSON should not crash."""
        place = Place(
            id="p5", source=PlaceSource.OSM,
            name="Mystery",
            coordinates=Coordinates(lat=42.0, lng=18.0),
            categories=[PlaceCategory.LANDMARK],
            category_confidence={PlaceCategory.LANDMARK.value: 0.3},
            metadata={"_needs_llm_classification": True},
        )

        with patch(
            "app.services.llm_client.chat_completion",
            new_callable=AsyncMock,
            return_value="not json at all",
        ):
            result = await classify_place_llm(place)
            assert PlaceCategory.LANDMARK in result.categories


# ── Storytelling API Tests ───────────────────────────────────


class TestStorytellingAPI:
    def test_story_valid_request(self):
        with patch(
            "app.api.storytelling.generate_place_story",
            new_callable=AsyncMock,
            return_value={
                "story": "The ancient walls whisper tales of forgotten glory...",
                "place_id": "p1",
                "place_name": "Old Fort",
                "ai_generated": True,
                "language": "en",
            },
        ):
            resp = client.post("/api/story", json={
                "place_id": "p1",
                "place_name": "Old Fort",
                "place_categories": ["ruins"],
                "lat": 42.65, "lng": 18.09,
                "language": "en",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "whisper" in data["story"]
            assert data["ai_generated"] is True

    def test_story_minimal_request(self):
        with patch(
            "app.api.storytelling.generate_place_story",
            new_callable=AsyncMock,
            return_value={
                "story": "A place to explore.",
                "place_id": "p2",
                "ai_generated": False,
                "language": "ru",
            },
        ):
            resp = client.post("/api/story", json={
                "place_id": "p2",
                "lat": 0.0, "lng": 0.0,
            })
            assert resp.status_code == 200

    def test_story_server_error(self):
        with patch(
            "app.api.storytelling.generate_place_story",
            new_callable=AsyncMock,
            side_effect=Exception("boom"),
        ):
            resp = client.post("/api/story", json={
                "place_id": "p1",
                "lat": 42.0, "lng": 18.0,
            })
            assert resp.status_code == 500

    def test_route_story_valid_request(self):
        with patch(
            "app.api.storytelling.generate_route_story",
            new_callable=AsyncMock,
            return_value={
                "story": "Your journey begins at the ancient fortress...",
                "place_ids": ["p1", "p2"],
                "ai_generated": True,
                "language": "en",
            },
        ):
            resp = client.post("/api/story/route", json={
                "places": [
                    {"place_id": "p1", "place_name": "Fort", "lat": 42.0, "lng": 18.0},
                    {"place_id": "p2", "place_name": "Cave", "lat": 42.01, "lng": 18.01},
                ],
                "language": "en",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["place_ids"]) == 2

    def test_route_story_too_few_places(self):
        resp = client.post("/api/story/route", json={
            "places": [
                {"place_id": "p1", "lat": 42.0, "lng": 18.0},
            ],
        })
        assert resp.status_code == 422  # min_length=2

    def test_route_story_server_error(self):
        with patch(
            "app.api.storytelling.generate_route_story",
            new_callable=AsyncMock,
            side_effect=Exception("error"),
        ):
            resp = client.post("/api/story/route", json={
                "places": [
                    {"place_id": "p1", "lat": 42.0, "lng": 18.0},
                    {"place_id": "p2", "lat": 42.01, "lng": 18.01},
                ],
            })
            assert resp.status_code == 500
