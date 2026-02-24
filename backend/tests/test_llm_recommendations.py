"""Tests for Story 2.3 — Contextual Recommendations."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.chat import (
    RecommendationRequest,
    UserPreferences,
)
from app.models.place import (
    Coordinates,
    DiscoverResponse,
    Place,
    PlaceCategory,
    PlaceSource,
)
from app.services.llm_recommendations import (
    _determine_strategy,
    _rule_based_ranking,
    get_recommendations,
)


def _make_place(
    pid: str,
    name: str,
    categories: list[PlaceCategory],
    confidence: float = 0.7,
    distance_m: float = 1000.0,
) -> Place:
    return Place(
        id=pid,
        source=PlaceSource.OSM,
        name=name,
        coordinates=Coordinates(lat=42.65, lng=18.09),
        categories=categories,
        confidence=confidence,
        distance_m=distance_m,
    )


# ── Strategy Determination ───────────────────────────────────


class TestDetermineStrategy:
    def test_cold_start_no_data(self):
        prefs = UserPreferences()
        assert _determine_strategy(prefs) == "cold_start"

    def test_personalized_with_favorites(self):
        prefs = UserPreferences(favorite_categories=[PlaceCategory.ABANDONED])
        assert _determine_strategy(prefs) == "personalized"

    def test_personalized_with_favorites_and_history(self):
        prefs = UserPreferences(
            favorite_categories=[PlaceCategory.RUINS],
            liked_place_ids=["p1", "p2"],
        )
        assert _determine_strategy(prefs) == "personalized"

    def test_diverse_with_history_only(self):
        prefs = UserPreferences(liked_place_ids=["p1"])
        assert _determine_strategy(prefs) == "diverse"


# ── Rule-Based Ranking ───────────────────────────────────────


class TestRuleBasedRanking:
    @pytest.fixture
    def candidates(self):
        return [
            _make_place("p1", "Abandoned Factory", [PlaceCategory.ABANDONED], 0.8, 500),
            _make_place("p2", "Old Church", [PlaceCategory.RELIGIOUS], 0.7, 1500),
            _make_place("p3", "Hidden Cave", [PlaceCategory.CAVE], 0.9, 3000),
            _make_place("p4", "City Park", [PlaceCategory.PARK], 0.6, 200),
            _make_place("p5", "War Bunker", [PlaceCategory.MILITARY], 0.75, 2000),
        ]

    def test_favorite_category_boost(self, candidates):
        req = RecommendationRequest(
            lat=42.65, lng=18.09, radius_km=5.0,
            preferences=UserPreferences(
                favorite_categories=[PlaceCategory.ABANDONED, PlaceCategory.MILITARY],
            ),
        )
        ranked = _rule_based_ranking(candidates, req)
        # Abandoned and Military should be ranked higher
        top_ids = [r.place.id for r in ranked[:2]]
        assert "p1" in top_ids  # Abandoned Factory

    def test_unusual_places_boosted(self, candidates):
        req = RecommendationRequest(
            lat=42.65, lng=18.09, radius_km=5.0,
            preferences=UserPreferences(),
        )
        ranked = _rule_based_ranking(candidates, req)
        # Unusual places should get a boost
        unusual_scores = [r.relevance_score for r in ranked if r.place.is_unusual]
        normal_scores = [r.relevance_score for r in ranked if not r.place.is_unusual]
        # At least one unusual should score higher than lowest normal
        if unusual_scores and normal_scores:
            assert max(unusual_scores) >= min(normal_scores)

    def test_diversity_penalty(self):
        # 3 abandoned places should trigger diversity penalty
        candidates = [
            _make_place(f"p{i}", f"Ruin {i}", [PlaceCategory.ABANDONED], 0.8)
            for i in range(5)
        ]
        req = RecommendationRequest(
            lat=42.65, lng=18.09, radius_km=5.0,
            preferences=UserPreferences(),
        )
        ranked = _rule_based_ranking(candidates, req)
        scores = [r.relevance_score for r in ranked]
        # Later same-category items should have lower scores
        assert scores[0] >= scores[-1]

    def test_distance_penalty(self, candidates):
        far_place = _make_place("far", "Far Place", [PlaceCategory.VIEWPOINT], 0.8, 8000)
        candidates.append(far_place)

        req = RecommendationRequest(
            lat=42.65, lng=18.09, radius_km=10.0,
            preferences=UserPreferences(),
        )
        ranked = _rule_based_ranking(candidates, req)
        far_rec = [r for r in ranked if r.place.id == "far"][0]
        # Far place should have distance penalty
        assert far_rec.relevance_score <= 0.9

    def test_reason_text_present(self, candidates):
        req = RecommendationRequest(
            lat=42.65, lng=18.09, radius_km=5.0,
            preferences=UserPreferences(
                favorite_categories=[PlaceCategory.CAVE],
            ),
        )
        ranked = _rule_based_ranking(candidates, req)
        cave_rec = [r for r in ranked if r.place.id == "p3"][0]
        assert "cave" in cave_rec.reason.lower()

    def test_empty_candidates(self):
        req = RecommendationRequest(
            lat=42.65, lng=18.09, radius_km=5.0,
            preferences=UserPreferences(),
        )
        ranked = _rule_based_ranking([], req)
        assert ranked == []


# ── Full Recommendation Pipeline ─────────────────────────────


class TestGetRecommendations:
    @pytest.fixture
    def mock_places(self):
        return [
            _make_place("p1", "Abandoned Fort", [PlaceCategory.ABANDONED, PlaceCategory.MILITARY], 0.85),
            _make_place("p2", "Hidden Beach", [PlaceCategory.NATURE_HIDDEN, PlaceCategory.WATER], 0.9),
            _make_place("p3", "Gothic Church", [PlaceCategory.RELIGIOUS], 0.7),
        ]

    @pytest.mark.asyncio
    async def test_cold_start_uses_rules(self, mock_places):
        req = RecommendationRequest(
            lat=42.65, lng=18.09, radius_km=5.0,
            preferences=UserPreferences(),
            limit=3,
        )

        with patch(
            "app.services.llm_recommendations.discover",
            new_callable=AsyncMock,
            return_value=DiscoverResponse(
                places=mock_places, total=3, has_more=False,
            ),
        ):
            result = await get_recommendations(req)
            assert result.strategy == "cold_start"
            assert len(result.recommendations) <= 3

    @pytest.mark.asyncio
    async def test_no_results(self):
        req = RecommendationRequest(
            lat=0.0, lng=0.0, radius_km=1.0,
            preferences=UserPreferences(),
        )

        with patch(
            "app.services.llm_recommendations.discover",
            new_callable=AsyncMock,
            return_value=DiscoverResponse(places=[], total=0, has_more=False),
        ):
            result = await get_recommendations(req)
            assert result.recommendations == []
            assert result.total == 0

    @pytest.mark.asyncio
    async def test_personalized_with_llm_fallback(self, mock_places):
        """When LLM fails, should fall back to rule-based ranking."""
        req = RecommendationRequest(
            lat=42.65, lng=18.09, radius_km=5.0,
            preferences=UserPreferences(
                favorite_categories=[PlaceCategory.ABANDONED],
                liked_place_ids=["old1"],
            ),
            limit=3,
        )

        with patch(
            "app.services.llm_recommendations.discover",
            new_callable=AsyncMock,
            return_value=DiscoverResponse(
                places=mock_places, total=3, has_more=False,
            ),
        ), patch(
            "app.services.llm_recommendations.chat_completion",
            new_callable=AsyncMock,
            side_effect=Exception("LLM unavailable"),
        ):
            result = await get_recommendations(req)
            assert result.strategy == "diverse"  # Falls back
            assert len(result.recommendations) > 0

    @pytest.mark.asyncio
    async def test_personalized_with_llm_success(self, mock_places):
        req = RecommendationRequest(
            lat=42.65, lng=18.09, radius_km=5.0,
            preferences=UserPreferences(
                favorite_categories=[PlaceCategory.ABANDONED],
            ),
            limit=2,
        )

        llm_response = '[{"place_index": 0, "reason": "Matches your love for abandoned places", "relevance_score": 0.95}, {"place_index": 1, "reason": "Hidden gem nearby", "relevance_score": 0.85}]'

        with patch(
            "app.services.llm_recommendations.discover",
            new_callable=AsyncMock,
            return_value=DiscoverResponse(
                places=mock_places, total=3, has_more=False,
            ),
        ), patch(
            "app.services.llm_recommendations.chat_completion",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            result = await get_recommendations(req)
            assert result.strategy == "personalized"
            assert len(result.recommendations) == 2
            assert result.recommendations[0].reason
