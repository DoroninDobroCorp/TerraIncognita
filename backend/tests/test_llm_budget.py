"""Daily LLM token budget: counting, persistence, enforcement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import llm_client


@pytest.fixture(autouse=True)
def _isolated_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client, "_daily_usage_path", tmp_path / "llm_usage.json")
    monkeypatch.setattr(llm_client, "_daily", {"date": "", "input": 0, "output": 0})
    monkeypatch.setattr(settings, "llm_daily_token_cap", 1000)


def test_no_cap_means_never_exhausted(monkeypatch):
    monkeypatch.setattr(settings, "llm_daily_token_cap", 0)
    llm_client._record_usage(10**6, 10**6)
    assert not llm_client.budget_exhausted()


def test_usage_accumulates_and_trips_cap():
    llm_client._record_usage(400, 100)
    assert llm_client.daily_tokens_used() == 500
    assert not llm_client.budget_exhausted()
    llm_client._record_usage(300, 200)
    assert llm_client.budget_exhausted()


def test_usage_survives_restart(tmp_path, monkeypatch):
    llm_client._record_usage(250, 250)
    # simulate a fresh process: in-memory state gone, file remains
    monkeypatch.setattr(llm_client, "_daily", {"date": "", "input": 0, "output": 0})
    assert llm_client.daily_tokens_used() == 500


def test_counters_roll_over_at_utc_midnight(monkeypatch):
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    monkeypatch.setattr(llm_client, "_daily", {"date": yesterday, "input": 999, "output": 999})
    assert llm_client.daily_tokens_used() == 0


async def test_chat_completion_refuses_when_over_budget(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    llm_client._record_usage(1000, 0)
    with patch.object(llm_client, "_get_client") as get_client:
        with pytest.raises(llm_client.LLMBudgetError):
            await llm_client.chat_completion([{"role": "user", "content": "hi"}])
        get_client.assert_not_called()


async def test_chat_completion_records_usage(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(llm_client, "_circuit_open_until", 0.0)
    response = MagicMock()
    response.usage.input_tokens, response.usage.output_tokens = 30, 12
    response.content = [SimpleNamespace(type="text", text="ok")]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    with patch.object(llm_client, "_get_client", return_value=client):
        assert await llm_client.chat_completion([{"role": "user", "content": "hi"}]) == "ok"
    assert llm_client.daily_tokens_used() == 42


def test_a_spent_budget_never_reaches_the_provider(monkeypatch):
    """Every LLM endpoint either degrades gracefully (fallback text) or answers 503 — none calls Claude."""
    from app.models.place import DiscoverResponse

    async def no_places(_req):
        return DiscoverResponse(places=[], total=0, has_more=False)

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(llm_client, "_circuit_open_until", 0.0)
    monkeypatch.setattr("app.services.llm_discovery.discover", no_places, raising=False)
    monkeypatch.setattr("app.services.llm_recommendations.discover", no_places)
    llm_client._record_usage(1000, 0)
    place = {"place_id": "p1", "place_name": "Old Fort", "lat": 42.4, "lng": 18.7, "place_categories": ["ruins"]}
    ask = {"place": {"name": "Old Fort", "lat": 42.4, "lng": 18.7, "categories": ["ruins"]},
           "question": "history?", "language": "en"}
    client = TestClient(app, raise_server_exceptions=False)
    with patch.object(llm_client, "_get_client") as get_client:
        assert client.post("/api/chat", json={"message": "hello", "lat": 1, "lng": 1}).status_code == 200
        assert client.post("/api/describe", json=place).status_code == 200
        assert client.post("/api/story", json=place).json()["ai_generated"] is False
        assert client.post("/api/recommend", json={"lat": 1, "lng": 1}).status_code == 200
        assert client.post("/api/place/ask", json=ask).status_code == 503
        get_client.assert_not_called()


def test_health_reports_budget():
    llm_client._record_usage(100, 50)
    usage = TestClient(app).get("/health").json()["llm_usage"]
    assert usage["daily_tokens_used"] == 150
    assert usage["daily_token_cap"] == 1000
    assert usage["budget_exhausted"] is False


async def test_openai_path_honours_explicit_zero_temperature(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_temperature", 0.7)
    monkeypatch.setattr(llm_client, "_circuit_open_until", 0.0)
    response = MagicMock()
    response.usage.prompt_tokens, response.usage.completion_tokens = 1, 1
    response.choices = [MagicMock(message=MagicMock(content="ok"))]
    oai = MagicMock()
    oai.chat.completions.create = AsyncMock(return_value=response)
    with patch.object(llm_client, "_get_openai_client", return_value=oai):
        await llm_client.chat_completion([{"role": "user", "content": "x"}], temperature=0.0)
    assert oai.chat.completions.create.call_args.kwargs["temperature"] == 0.0
