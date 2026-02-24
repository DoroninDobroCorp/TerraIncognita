"""Tests for Epic 2 API endpoints — Chat, Describe, Recommend."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.chat import (
    ChatResponse,
    DescriptionResponse,
    RecommendationResponse,
)
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource

client = TestClient(app)


def _mock_place(pid: str = "p1", name: str = "Test Place") -> Place:
    return Place(
        id=pid, source=PlaceSource.OSM, name=name,
        coordinates=Coordinates(lat=42.65, lng=18.09),
        categories=[PlaceCategory.ABANDONED],
        confidence=0.8, distance_m=500.0,
    )


# ── Chat API ─────────────────────────────────────────────────


class TestChatAPI:
    def test_chat_valid_request(self):
        with patch(
            "app.api.chat.process_chat",
            new_callable=AsyncMock,
            return_value=ChatResponse(
                message="Found some cool places!",
                places=[_mock_place()],
                conversation_id="conv-1",
                language="en",
            ),
        ):
            resp = client.post("/api/chat", json={
                "message": "show me abandoned places",
                "lat": 42.65, "lng": 18.09,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["message"] == "Found some cool places!"
            assert len(data["places"]) == 1
            assert data["conversation_id"] == "conv-1"

    def test_chat_empty_message_rejected(self):
        resp = client.post("/api/chat", json={
            "message": "",
            "lat": 42.65, "lng": 18.09,
        })
        assert resp.status_code == 422

    def test_chat_invalid_coordinates(self):
        resp = client.post("/api/chat", json={
            "message": "find me something",
            "lat": 999, "lng": 18.09,
        })
        assert resp.status_code == 422

    def test_chat_with_history(self):
        with patch(
            "app.api.chat.process_chat",
            new_callable=AsyncMock,
            return_value=ChatResponse(
                message="Closer places:",
                places=[],
                conversation_id="conv-1",
                language="ru",
            ),
        ):
            resp = client.post("/api/chat", json={
                "message": "а поближе?",
                "lat": 42.65, "lng": 18.09,
                "history": [
                    {"role": "user", "content": "покажи заброшки"},
                    {"role": "assistant", "content": "Вот что нашёл!"},
                ],
                "conversation_id": "conv-1",
            })
            assert resp.status_code == 200

    def test_chat_server_error(self):
        with patch(
            "app.api.chat.process_chat",
            new_callable=AsyncMock,
            side_effect=Exception("boom"),
        ):
            resp = client.post("/api/chat", json={
                "message": "test",
                "lat": 42.65, "lng": 18.09,
            })
            assert resp.status_code == 500


# ── Describe API ─────────────────────────────────────────────


class TestDescribeAPI:
    def test_describe_valid_request(self):
        with patch(
            "app.api.descriptions.generate_description",
            new_callable=AsyncMock,
            return_value=DescriptionResponse(
                place_id="p1",
                description="A haunting ruin by the sea.",
                practical_info="Best visited in summer.",
                ai_generated=True,
            ),
        ):
            resp = client.post("/api/describe", json={
                "place_id": "p1",
                "place_name": "Old Fort",
                "lat": 42.65, "lng": 18.09,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["description"] == "A haunting ruin by the sea."
            assert data["ai_generated"] is True

    def test_describe_minimal_request(self):
        with patch(
            "app.api.descriptions.generate_description",
            new_callable=AsyncMock,
            return_value=DescriptionResponse(
                place_id="p2",
                description="An interesting spot.",
            ),
        ):
            resp = client.post("/api/describe", json={
                "place_id": "p2",
                "lat": 0.0, "lng": 0.0,
            })
            assert resp.status_code == 200

    def test_describe_server_error(self):
        with patch(
            "app.api.descriptions.generate_description",
            new_callable=AsyncMock,
            side_effect=Exception("LLM down"),
        ):
            resp = client.post("/api/describe", json={
                "place_id": "p1",
                "lat": 42.65, "lng": 18.09,
            })
            assert resp.status_code == 500


# ── Recommend API ────────────────────────────────────────────


class TestRecommendAPI:
    def test_recommend_valid_request(self):
        from app.models.chat import RecommendedPlace
        with patch(
            "app.api.recommendations.get_recommendations",
            new_callable=AsyncMock,
            return_value=RecommendationResponse(
                recommendations=[
                    RecommendedPlace(
                        place=_mock_place(),
                        reason="Great abandoned spot",
                        relevance_score=0.9,
                    )
                ],
                total=1,
                strategy="cold_start",
            ),
        ):
            resp = client.post("/api/recommend", json={
                "lat": 42.65, "lng": 18.09,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["strategy"] == "cold_start"
            assert len(data["recommendations"]) == 1

    def test_recommend_with_preferences(self):
        from app.models.chat import RecommendedPlace
        with patch(
            "app.api.recommendations.get_recommendations",
            new_callable=AsyncMock,
            return_value=RecommendationResponse(
                recommendations=[
                    RecommendedPlace(
                        place=_mock_place(),
                        reason="Matches your interest",
                        relevance_score=0.95,
                    )
                ],
                total=1,
                strategy="personalized",
            ),
        ):
            resp = client.post("/api/recommend", json={
                "lat": 42.65, "lng": 18.09,
                "preferences": {
                    "favorite_categories": ["abandoned", "ruins"],
                    "visited_place_ids": ["old1"],
                    "liked_place_ids": ["old1"],
                },
                "limit": 5,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["strategy"] == "personalized"

    def test_recommend_server_error(self):
        with patch(
            "app.api.recommendations.get_recommendations",
            new_callable=AsyncMock,
            side_effect=Exception("engine error"),
        ):
            resp = client.post("/api/recommend", json={
                "lat": 42.65, "lng": 18.09,
            })
            assert resp.status_code == 500
