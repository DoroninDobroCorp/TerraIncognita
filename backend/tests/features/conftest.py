"""Fixtures shared by the feature tests (daily place, loops, collections, streak, ask).

Nothing here touches the network, the real LLM, or the real data/cache directories.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services import gamification as gam_service
from app.services import journal, llm_client, llm_support


@pytest.fixture(autouse=True)
def _isolated_caches(tmp_path, monkeypatch):
    """Every test gets empty disk caches and no leftover in-flight LLM calls."""
    monkeypatch.setattr(settings, "cache_dir", str(tmp_path / "cache"))
    llm_support._inflight.clear()
    yield
    llm_support._inflight.clear()


@pytest.fixture
def fresh_journal():
    """Empty journal + gamification stores backed by temp dirs (same trick as test_journal)."""
    journal._reset_store()
    gam_service._reset_store()
    yield journal
    journal._reset_store()
    gam_service._reset_store()


@pytest.fixture
def llm_ready(monkeypatch):
    """LLM 'configured' and healthy; chat_completion replaced by a mock returning text."""
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_daily_token_cap", 0)
    monkeypatch.setattr(llm_client, "_circuit_open_until", 0.0)
    mock = AsyncMock(return_value="A short, grounded LLM answer.")
    monkeypatch.setattr(llm_client, "chat_completion", mock)
    return mock


@pytest.fixture
def no_llm_key(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(llm_client, "_circuit_open_until", 0.0)
    mock = AsyncMock(side_effect=AssertionError("LLM must not be called without a key"))
    monkeypatch.setattr(llm_client, "chat_completion", mock)
    return mock
