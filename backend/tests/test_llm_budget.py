"""Daily LLM token budget: counting, persistence, enforcement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    response.content = [MagicMock(text="ok")]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    with patch.object(llm_client, "_get_client", return_value=client):
        assert await llm_client.chat_completion([{"role": "user", "content": "hi"}]) == "ok"
    assert llm_client.daily_tokens_used() == 42


def test_llm_endpoints_return_503_when_budget_spent():
    llm_client._record_usage(1000, 0)
    resp = TestClient(app).post("/api/chat", json={"message": "hello", "lat": 1, "lng": 1})
    assert resp.status_code == 503
    assert int(resp.headers["retry-after"]) > 0


def test_health_reports_budget():
    llm_client._record_usage(100, 50)
    usage = TestClient(app).get("/health").json()["llm_usage"]
    assert usage["daily_tokens_used"] == 150
    assert usage["daily_token_cap"] == 1000
    assert usage["budget_exhausted"] is False
