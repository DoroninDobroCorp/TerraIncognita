"""Deep Research source: category guessing, de-duplicated background runs, cache hygiene."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.cache import DiskCache
from app.sources import deep_research as dr
from app.sources.deep_research import DeepResearchSource

C = PlaceCategory


@pytest.mark.parametrize(("text", "hint", "expected", "unexpected"), [
    ("A cosy cafe on Trinity Street", "", set(), {C.NATURE_HIDDEN}),          # "tree" in "street"
    ("Comfort Inn by the road", "", set(), {C.MILITARY}),                       # "fort" in "comfort"
    ("Parking garage", "", set(), {C.PARK}),
    ("Radish and dishwasher museum", "", {C.MUSEUM}, {C.CULINARY}),            # "dish" in "radish"
    ("Old fort above the bay", "", {C.MILITARY, C.WATER}, set()),
    ("Austro-Hungarian fortress", "", {C.MILITARY, C.LANDMARK}, set()),
    ("Abandoned factories and forgotten tunnels", "", {C.ABANDONED, C.INDUSTRIAL, C.UNDERGROUND}, set()),
    ("Cold-war bunkers", "", {C.MILITARY}, set()),
    ("Hidden caves", "", {C.CAVE}, set()),
    ("Medieval monasteries", "", {C.RELIGIOUS}, set()),
    ("Ruined chapel", "", {C.RUINS}, set()),
    ("Shipwreck dive", "underwater", {C.UNDERWATER}, set()),
    ("Something", "street art", {C.STREET_ART}, set()),                         # explicit hint wins
])
def test_guess_categories_uses_whole_words(text, hint, expected, unexpected):
    cats = set(DeepResearchSource()._guess_categories(text, hint))
    assert expected <= cats
    assert not cats & unexpected


@pytest.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "_dr_cache", DiskCache(cache_dir=str(tmp_path / "dr"), ttl=3600))
    monkeypatch.setattr(dr, "_nothing_found", DiskCache(cache_dir=str(tmp_path / "none"), ttl=3600))
    monkeypatch.setattr(dr, "_running", {})
    monkeypatch.setattr(settings, "deep_research_enabled", True)
    monkeypatch.setattr(settings, "parallel_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_api_key", "")

    source = DeepResearchSource()
    found = [Place(id="dr_x_1", source=PlaceSource.DEEP_RESEARCH, name="Secret Grotto",
                   coordinates=Coordinates(lat=42.4, lng=18.5), categories=[C.CAVE])]
    with patch.object(source, "_reverse_geocode", new=AsyncMock(return_value="Kotor")) as geo_city, \
         patch.object(source, "_guess_country", new=AsyncMock(return_value="Montenegro")) as geo_country, \
         patch.object(source, "_research_parallel", new=AsyncMock(return_value=found)) as parallel, \
         patch.object(source, "_research_gemini", new=AsyncMock(return_value=[])) as gemini:
        yield source, {"city": geo_city, "country": geo_country, "parallel": parallel,
                       "gemini": gemini, "found": found}


async def finish_background_runs():
    await asyncio.gather(*list(dr._running.values()))
    await asyncio.sleep(0)  # let done-callbacks run


class TestBackgroundRuns:
    async def test_without_api_keys_nothing_is_started_or_cached(self, env, monkeypatch):
        source, mocks = env
        monkeypatch.setattr(settings, "parallel_api_key", "")
        assert await source.search(42.4, 18.5, 5) == []
        assert dr._running == {}
        assert await source.get_status(42.4, 18.5, 5) == ("idle", None)
        mocks["city"].assert_not_called()

    async def test_disabled_switch_wins(self, env, monkeypatch):
        source, _ = env
        monkeypatch.setattr(settings, "deep_research_enabled", False)
        assert await source.search(42.4, 18.5, 5) == []
        assert await source.search_sync(42.4, 18.5, 5) == []
        assert dr._running == {}

    async def test_repeated_requests_start_one_run(self, env):
        source, mocks = env
        release = asyncio.Event()

        async def slow(*_args, **_kwargs):
            await release.wait()
            return mocks["found"]

        mocks["parallel"].side_effect = slow
        for _ in range(5):  # the map pans and zooms while a run takes minutes
            assert await source.search(42.4, 18.5, 5) == []
        status, message = await source.get_status(42.4, 18.5, 5)
        assert status == "pending" and "in progress" in message
        release.set()
        await finish_background_runs()
        assert mocks["parallel"].await_count == 1
        assert dr._running == {}

    async def test_finished_run_is_cached_and_served(self, env):
        source, mocks = env
        await source.search(42.4, 18.5, 5)
        await finish_background_runs()
        places = await source.search(42.4, 18.5, 5)
        assert [p.name for p in places] == ["Secret Grotto"]
        assert (await source.get_status(42.4, 18.5, 5))[0] == "cached"
        assert mocks["parallel"].await_count == 1

    async def test_geocoding_happens_once_and_is_shared(self, env):
        source, mocks = env
        await source.search_sync(42.4, 18.5, 5)
        mocks["city"].assert_awaited_once()
        mocks["country"].assert_awaited_once()
        args = mocks["parallel"].await_args.args
        assert args[3:] == ("Kotor", "Montenegro")
        assert mocks["gemini"].await_args.args[3:] == ("Kotor", "Montenegro")

    async def test_empty_result_is_not_cached_for_30_days_and_not_rerun_at_once(self, env):
        source, mocks = env
        mocks["parallel"].return_value = []  # e.g. both providers failed or timed out
        await source.search(42.4, 18.5, 5)
        await finish_background_runs()
        assert await dr._dr_cache.get("dr_42.40_18.50_5") is None
        assert await dr._nothing_found.get("dr_42.40_18.50_5") is not None
        await source.search(42.4, 18.5, 5)  # soon after: no new paid run
        await finish_background_runs()
        assert mocks["parallel"].await_count == 1
        assert await source.get_status(42.4, 18.5, 5) == ("idle", None)

    async def test_retries_after_the_negative_cache_expires(self, env):
        source, mocks = env
        mocks["parallel"].return_value = []
        await source.search(42.4, 18.5, 5)
        await finish_background_runs()
        await dr._nothing_found.invalidate("dr_42.40_18.50_5")
        mocks["parallel"].return_value = mocks["found"]
        await source.search(42.4, 18.5, 5)
        await finish_background_runs()
        assert [p.name for p in await source.search(42.4, 18.5, 5)] == ["Secret Grotto"]

    async def test_a_crashing_run_does_not_leak_the_running_marker(self, env):
        source, mocks = env
        mocks["city"].side_effect = RuntimeError("geocoder exploded")
        await source.search(42.4, 18.5, 5)
        with pytest.raises(RuntimeError):
            await asyncio.gather(*list(dr._running.values()))
        await asyncio.sleep(0)
        assert dr._running == {}
