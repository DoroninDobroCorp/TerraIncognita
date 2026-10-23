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


def test_production_caches_use_separate_directories():
    """Same keys in one directory would make the 30-minute 'found nothing' marker a 30-day answer."""
    assert dr._dr_cache._dir != dr._nothing_found._dir


class TestParsingResearchText:
    source = DeepResearchSource()

    def test_a_json_array_with_nested_values(self):
        text = 'Here you go: [{"name": "A", "lat": 42.1, "lng": 18.2, "tags": ["x", "y"]}, {"name": "B", "lat": 42.2, "lng": 18.3}] done'
        assert [p.name for p in self.source._parse_research_text(text, "gemini")] == ["A", "B"]

    def test_loose_objects_in_the_middle_of_prose(self):
        text = 'see {"name": "C", "lat": 42.1, "lng": 18.2} and {"name": "D", "lat": 42.3, "lng": 18.4}.'
        assert [p.name for p in self.source._parse_research_text(text, "gemini")] == ["C", "D"]

    def test_a_place_inside_a_wrapper_object(self):
        text = '{"wrapper": {"name": "E", "lat": 42.1, "lng": 18.2}, "count": 1}'
        assert [p.name for p in self.source._parse_research_text(text, "gemini")] == ["E"]

    def test_markdown_lines_give_the_whole_name_and_every_place(self):
        """The old pattern took a one-letter name and stopped after the first match."""
        text = (
            "## Report\n"
            "**Fort Mamula** - old fort at 42.4381, 18.5694 on the island\n"
            "### Hidden Cave (42.5123, 18.6123)\n"
            "- a line without a name 42.1111, 18.2222\n"
            "**Out of range** 95.1234, 18.1234\n"
        )
        found = {p.name: (p.coordinates.lat, p.coordinates.lng) for p in self.source._parse_research_text(text, "gemini")}
        assert found == {"Fort Mamula": (42.4381, 18.5694), "Hidden Cave": (42.5123, 18.6123)}

    @pytest.mark.parametrize("text", [
        "[" * 100_000, "{" * 100_000, "# **" * 25_000, "[{" * 50_000, '{"' * 50_000, '{"a":[' * 20_000,
        "## Section\n**Bold** text without coordinates, " * 3_000, "**x** 12.345 " * 10_000,
    ])
    def test_hostile_text_costs_next_to_nothing(self, text):
        import time

        t0 = time.process_time()
        self.source._parse_research_text(text, "gemini")
        assert time.process_time() - t0 < 1.0


class TestEmptyEntriesFromTheOldCache:
    async def test_an_empty_list_is_not_a_cache_hit(self, env):
        """The old 30-day cache stored [] when both providers failed: that pinned 'nothing here' for a month."""
        source, mocks = env
        await dr._dr_cache.set("dr_42.40_18.50_5", [])
        assert await source.get_status(42.4, 18.5, 5) == ("idle", None)
        assert await source.search(42.4, 18.5, 5) == []
        await finish_background_runs()
        assert mocks["parallel"].await_count == 1  # it asked again
        assert [p.name for p in await source.search(42.4, 18.5, 5)] == ["Secret Grotto"]

    async def test_search_sync_asks_again_too(self, env):
        source, mocks = env
        await dr._dr_cache.set("dr_42.40_18.50_5", [])
        assert [p.name for p in await source.search_sync(42.4, 18.5, 5)] == ["Secret Grotto"]


class TestNominatimLookups:
    @pytest.fixture
    async def geocoding(self, tmp_path, monkeypatch):
        from app.utils import http_client
        from app.utils.rate_limiter import RateLimiter

        monkeypatch.setattr(dr, "_geocode_cache", DiskCache(cache_dir=str(tmp_path / "geo"), ttl=0))
        monkeypatch.setattr(dr, "_nominatim_limiter", RateLimiter(max_requests=1, window_seconds=0.0))
        http_client._client = None
        yield DeepResearchSource()
        await http_client.close_http_client()

    async def test_a_place_is_looked_up_once_and_with_a_polite_user_agent(self, geocoding, httpx_mock):
        httpx_mock.add_response(json={"address": {"town": "Herceg Novi", "country": "Montenegro"}})
        assert await geocoding._reverse_geocode(42.4531, 18.5375) == "Herceg Novi"
        assert await geocoding._reverse_geocode(42.4531, 18.5375) == "Herceg Novi"
        [request] = httpx_mock.get_requests()
        assert request.headers["user-agent"] == settings.wikimedia_user_agent
        assert "nominatim.openstreetmap.org" in str(request.url)

    async def test_city_and_country_are_two_different_lookups(self, geocoding, httpx_mock):
        for _ in range(2):  # one answer per request
            httpx_mock.add_response(json={"address": {"town": "Herceg Novi", "country": "Montenegro"}})
        await geocoding._reverse_geocode(42.4531, 18.5375)
        assert await geocoding._guess_country(42.4531, 18.5375) == "Montenegro"
        assert len(httpx_mock.get_requests()) == 2

    async def test_lookups_are_spaced_out_to_respect_the_usage_policy(self, geocoding, httpx_mock, monkeypatch):
        import time

        from app.utils.rate_limiter import RateLimiter

        monkeypatch.setattr(dr, "_nominatim_limiter", RateLimiter(max_requests=1, window_seconds=0.3))
        for _ in range(2):
            httpx_mock.add_response(json={"address": {"town": "T", "country": "C"}})
        t0 = time.monotonic()
        await geocoding._reverse_geocode(42.1, 18.1)
        await geocoding._guess_country(42.1, 18.1)
        assert time.monotonic() - t0 >= 0.25

    async def test_a_failure_is_not_cached(self, geocoding, httpx_mock):
        import httpx

        httpx_mock.add_exception(httpx.ConnectError("down"))
        assert await geocoding._reverse_geocode(42.4531, 18.5375) == "Unknown"
        httpx_mock.add_response(json={"address": {"city": "Kotor"}})
        assert await geocoding._reverse_geocode(42.4531, 18.5375) == "Kotor"
