"""Tests for LLM endpoint rate limiting and boundary conditions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestChatBoundary:
    """Boundary condition tests for /api/chat endpoint."""

    def test_max_length_message(self):
        """Message at exactly 2000 chars should be accepted."""
        with patch(
            "app.api.chat.process_chat",
            new_callable=AsyncMock,
        ) as mock:
            from app.models.chat import ChatResponse
            mock.return_value = ChatResponse(
                message="ok", conversation_id="c1", language="en",
            )
            resp = client.post("/api/chat", json={
                "message": "a" * 2000,
                "lat": 42.0, "lng": 18.0,
            })
            assert resp.status_code == 200

    def test_over_max_length_message(self):
        """Message over 2000 chars should be rejected."""
        resp = client.post("/api/chat", json={
            "message": "a" * 2001,
            "lat": 42.0, "lng": 18.0,
        })
        assert resp.status_code == 422

    def test_invalid_language_code(self):
        """Invalid language code should be rejected."""
        resp = client.post("/api/chat", json={
            "message": "test",
            "lat": 42.0, "lng": 18.0,
            "language": "invalid_lang",
        })
        assert resp.status_code == 422

    def test_valid_language_codes(self):
        """Various valid language codes should be accepted."""
        with patch(
            "app.api.chat.process_chat",
            new_callable=AsyncMock,
        ) as mock:
            from app.models.chat import ChatResponse
            mock.return_value = ChatResponse(
                message="ok", conversation_id="c1", language="en",
            )
            for lang in ["auto", "ru", "en", "de", "fr"]:
                resp = client.post("/api/chat", json={
                    "message": "test",
                    "lat": 42.0, "lng": 18.0,
                    "language": lang,
                })
                assert resp.status_code == 200, f"Failed for language: {lang}"

    def test_max_history_length(self):
        """Should accept up to 20 history messages."""
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
            for i in range(20)
        ]
        with patch(
            "app.api.chat.process_chat",
            new_callable=AsyncMock,
        ) as mock:
            from app.models.chat import ChatResponse
            mock.return_value = ChatResponse(
                message="ok", conversation_id="c1", language="en",
            )
            resp = client.post("/api/chat", json={
                "message": "test",
                "lat": 42.0, "lng": 18.0,
                "history": history,
            })
            assert resp.status_code == 200


class TestDescribeBoundary:
    def test_describe_with_all_categories(self):
        """Should accept all valid category values."""
        with patch(
            "app.api.descriptions.generate_description",
            new_callable=AsyncMock,
        ) as mock:
            from app.models.chat import DescriptionResponse
            mock.return_value = DescriptionResponse(
                place_id="p1", description="test",
            )
            resp = client.post("/api/describe", json={
                "place_id": "p1",
                "place_categories": ["abandoned", "ruins", "cave"],
                "lat": 42.0, "lng": 18.0,
            })
            assert resp.status_code == 200


class TestRecommendBoundary:
    def test_recommend_with_max_visited(self):
        """Should accept large visited_place_ids list."""
        with patch(
            "app.api.recommendations.get_recommendations",
            new_callable=AsyncMock,
        ) as mock:
            from app.models.chat import RecommendationResponse
            mock.return_value = RecommendationResponse(
                recommendations=[], total=0, strategy="cold_start",
            )
            resp = client.post("/api/recommend", json={
                "lat": 42.0, "lng": 18.0,
                "preferences": {
                    "visited_place_ids": [f"p{i}" for i in range(100)],
                },
            })
            assert resp.status_code == 200


class TestHealthEndpointLLM:
    def test_health_includes_llm_info(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "llm" in data
        assert "llm_usage" in data
        assert "status" in data["llm"]
