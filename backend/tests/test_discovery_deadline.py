"""Discovery answers with what has arrived by the deadline; stragglers warm the cache."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models.place import Coordinates, DiscoverRequest, Place, PlaceCategory, PlaceSource
from app.services import discovery

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
