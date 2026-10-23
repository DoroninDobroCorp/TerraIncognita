"""Discovery answers with what has arrived by the deadline; stragglers warm the cache."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models.place import Coordinates, DiscoverRequest, Place, PlaceCategory, PlaceSource
from app.services import discovery
from app.sources.base import PartialSourceResult, SourceError

_NAMES = {"a": "Fort Mamula", "b": "Kotor Aqueduct", "osm1": "Hill Bunker", "wiki1": "Old Lighthouse"}


def _place(pid: str, source: PlaceSource = PlaceSource.OSM) -> Place:
    # distinct names AND positions, so fusion never folds two test places into one
    offset = 0.02 * (sorted(_NAMES).index(pid) + 1)
    return Place(
        id=pid, source=source, name=_NAMES[pid], categories=[PlaceCategory.RUINS],
        coordinates=Coordinates(lat=42.45 + offset, lng=18.53 + offset),
    )


class FakeSource:
    def __init__(self, name: str, places: list[Place], delay: float = 0.0, fail: bool = False):
        self.source_name = name
        self._places, self._delay, self._fail = places, delay, fail
        self.finished = False

    async def search(self, lat, lng, radius_km):
        await asyncio.sleep(self._delay)
        self.finished = True
        if self._fail:
            raise RuntimeError("boom")
        return self._places


REQ = DiscoverRequest(lat=42.45, lng=18.53, radius_km=5)


def _patched(osm, atlas, wiki, deep=None):
    deep = deep or FakeSource("deep", [])
    deep.get_status = AsyncMock(return_value=("idle", None))
    return (
        patch.object(discovery, "_osm", osm), patch.object(discovery, "_atlas", atlas),
        patch.object(discovery, "_wiki", wiki), patch.object(discovery, "_deep", deep),
    )


async def _run(osm, atlas, wiki):
    p1, p2, p3, p4 = _patched(osm, atlas, wiki)
    with p1, p2, p3, p4:
        return await discovery.discover(REQ)


async def test_slow_source_does_not_hold_the_answer(monkeypatch):
    monkeypatch.setattr(settings, "discovery_deadline_seconds", 0.05)
    slow = FakeSource("osm", [_place("osm1")], delay=0.5)
    fast = FakeSource("wiki", [_place("wiki1", PlaceSource.WIKIDATA)])
    resp = await _run(slow, FakeSource("atlas", []), fast)
    assert [p.id for p in resp.places] == ["wiki1"]
    assert resp.source_status["osm"] == "timeout"
    assert resp.source_status["wiki"] == "ok"
    assert not slow.finished  # we did not wait for it...
    await asyncio.sleep(0.6)
    assert slow.finished  # ...but it kept running to warm the cache
    assert not discovery._background_tasks


async def test_waits_for_first_useful_source_when_nothing_has_arrived(monkeypatch):
    monkeypatch.setattr(settings, "discovery_deadline_seconds", 0.05)
    resp = await _run(
        FakeSource("osm", [_place("osm1")], delay=0.2),
        FakeSource("atlas", [], delay=0.01),  # finishes in time, but has nothing
        FakeSource("wiki", [], delay=0.01),
    )
    assert [p.id for p in resp.places] == ["osm1"]
    assert resp.source_status["osm"] == "ok"


async def test_everyone_fast_means_no_timeouts(monkeypatch):
    monkeypatch.setattr(settings, "discovery_deadline_seconds", 5)
    resp = await _run(
        FakeSource("osm", [_place("a")]), FakeSource("atlas", [], fail=True),
        FakeSource("wiki", [_place("b", PlaceSource.WIKIDATA)]),
    )
    assert {p.id for p in resp.places} == {"a", "b"}
    assert resp.source_status == {
        "osm": "ok", "atlas": "error", "wiki": "ok", "deep_research": "empty",
    }


async def test_all_sources_failing_returns_an_empty_answer(monkeypatch):
    monkeypatch.setattr(settings, "discovery_deadline_seconds", 0.05)
    resp = await _run(
        FakeSource("osm", [], fail=True), FakeSource("atlas", [], fail=True),
        FakeSource("wiki", [], delay=0.1),
    )
    assert resp.places == [] and resp.total == 0
    assert resp.source_status["osm"] == "error"


@pytest.fixture(autouse=True)
def _no_leftover_tasks():
    yield
    discovery._background_tasks.clear()


class TestMaxWait:
    async def test_waiting_for_a_first_useful_source_is_bounded(self, monkeypatch):
        """Nothing useful has arrived at the deadline; we still must answer before the client gives up."""
        monkeypatch.setattr(settings, "discovery_deadline_seconds", 0.05)
        monkeypatch.setattr(settings, "discovery_max_wait_seconds", 0.15)
        slow = FakeSource("osm", [_place("osm1")], delay=2.0)
        resp = await _run(slow, FakeSource("atlas", []), FakeSource("wiki", [], delay=0.01))
        assert resp.places == [] and resp.source_status["osm"] == "timeout"
        assert not slow.finished  # still running in the background, warming the cache
        discovery._background_tasks.clear()


class TestSourceOutcomes:
    async def test_partial_result_is_used_and_reported(self):
        class Partial(FakeSource):
            async def search(self, lat, lng, radius_km):
                raise PartialSourceResult([_place("osm1")], "gems tier failed")

        resp = await _run(Partial("osm", []), FakeSource("atlas", []), FakeSource("wiki", []))
        assert [p.id for p in resp.places] == ["osm1"]
        assert resp.source_status["osm"] == "partial"

    async def test_expected_failures_log_one_line_without_a_traceback(self, caplog):
        class Blocked(FakeSource):
            async def search(self, lat, lng, radius_km):
                raise SourceError("blocked (403)")

        with caplog.at_level("WARNING", logger="app.services.discovery"):
            resp = await _run(FakeSource("osm", [_place("osm1")]), Blocked("atlas", []), FakeSource("wiki", []))
        assert resp.source_status["atlas"] == "error"
        records = [r for r in caplog.records if "atlas" in r.getMessage()]
        assert records and all(r.exc_info is None for r in records)

    async def test_unexpected_failures_keep_their_traceback(self, caplog):
        with caplog.at_level("ERROR", logger="app.services.discovery"):
            await _run(FakeSource("osm", [], fail=True), FakeSource("atlas", []), FakeSource("wiki", []))
        assert any(r.exc_info for r in caplog.records)


class TestPaginationBoundary:
    async def test_has_more_is_false_when_the_last_page_ends_exactly_at_the_total(self):
        places = [_place(pid) for pid in ("a", "b")]
        p1, p2, p3, p4 = _patched(FakeSource("osm", places), FakeSource("atlas", []), FakeSource("wiki", []))
        with p1, p2, p3, p4:
            page = await discovery.discover(DiscoverRequest(lat=42.45, lng=18.53, radius_km=5, limit=2))
        assert page.total == 2 and page.has_more is False and page.cursor is None


class TestBookkeeping:
    async def test_a_straggler_is_tracked_when_the_answer_goes_out(self, monkeypatch):
        """Without a strong reference the loop may garbage-collect the task mid-flight."""
        monkeypatch.setattr(settings, "discovery_deadline_seconds", 0.05)
        slow = FakeSource("osm", [_place("osm1")], delay=0.5)
        await _run(slow, FakeSource("atlas", []), FakeSource("wiki", [_place("wiki1", PlaceSource.WIKIDATA)]))
        assert len(discovery._background_tasks) == 1
        await asyncio.sleep(0.6)
        assert slow.finished and not discovery._background_tasks

    async def test_the_first_wait_is_capped_by_max_wait_too(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_deadline_seconds", 5.0)
        monkeypatch.setattr(settings, "discovery_max_wait_seconds", 0.1)
        started = time.monotonic()
        resp = await _run(FakeSource("osm", [], delay=2.0), FakeSource("atlas", []), FakeSource("wiki", [], delay=2.0))
        assert time.monotonic() - started < 1.0
        assert resp.source_status["osm"] == "timeout"
        discovery._background_tasks.clear()
