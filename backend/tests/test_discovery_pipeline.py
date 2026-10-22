"""Discovery pipeline: scoring, sort_by=unusual, source status, timeouts (no network)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.place import (
    Coordinates,
    DiscoverRequest,
    DiscoverResponse,
    Place,
    PlaceCategory,
    PlaceSource,
)
from app.services.discovery import discover, discover_places_from_sources
from app.sources.osm import OSMSource

C = PlaceCategory


def osm_places(*tag_sets: dict, start_id: int = 1) -> list[Place]:
    elements = [
        {"type": "node", "id": start_id + i, "lat": 42.45 + i * 0.01, "lon": 18.53, "tags": tags}
        for i, tags in enumerate(tag_sets)
    ]
    return OSMSource()._parse_elements(elements)


@pytest.fixture
def sources():
    """Replace the four source singletons; yields the mocks keyed by name."""
    mocks = {name: MagicMock(source_name=name) for name in ("osm", "atlas", "wiki", "deep")}
    for m in mocks.values():
        m.search = AsyncMock(return_value=[])
    mocks["deep"].get_status = AsyncMock(return_value=("idle", None))
    with patch("app.services.discovery._osm", mocks["osm"]), \
         patch("app.services.discovery._atlas", mocks["atlas"]), \
         patch("app.services.discovery._wiki", mocks["wiki"]), \
         patch("app.services.discovery._deep", mocks["deep"]):
        yield mocks


REQ = {"lat": 42.45, "lng": 18.53, "radius_km": 5.0}


class TestScoringInPipeline:
    async def test_every_place_is_scored(self, sources):
        sources["osm"].search.return_value = osm_places(
            {"military": "bunker", "abandoned": "yes", "name": "Zlatica"},
            {"tourism": "museum", "name": "City Museum"},
        )
        result = await discover(DiscoverRequest(**REQ))
        by_name = {p.name: p for p in result.places}
        assert by_name["Zlatica"].unusual_score > 0.6
        assert "military bunker" in by_name["Zlatica"].unusual_reasons
        assert by_name["City Museum"].unusual_score < 0.2

    async def test_sort_by_unusual(self, sources):
        sources["osm"].search.return_value = osm_places(
            {"tourism": "museum", "name": "City Museum", "wikipedia": "en:City_Museum"},
            {"building": "church", "name": "St Nicholas"},
            {"military": "bunker", "abandoned": "yes", "name": "Zlatica"},
            {"natural": "cave_entrance", "name": "Blue Grotto"},
        )
        result = await discover(DiscoverRequest(**REQ, sort_by="unusual"))
        names = [p.name for p in result.places]
        assert set(names[:2]) == {"Zlatica", "Blue Grotto"}
        assert names[-1] in {"City Museum", "St Nicholas"}
        scores = [p.unusual_score for p in result.places]
        assert scores == sorted(scores, reverse=True)

    async def test_default_sort_is_still_confidence(self, sources):
        sources["osm"].search.return_value = osm_places(
            {"tourism": "museum", "name": "City Museum"}, {"military": "bunker", "name": "Z"})
        result = await discover(DiscoverRequest(**REQ))
        confs = [p.confidence for p in result.places]
        assert confs == sorted(confs, reverse=True)

    async def test_unusual_sort_spreads_a_ruin_complex(self, sources):
        # eleven ruined buildings of one fort + two distant gems: the gems must not be buried
        fort = [{"historic": "ruins", "name": f"Barracks block {chr(65 + i)}"} for i in range(11)]
        places = osm_places(*fort)
        for i, p in enumerate(places):  # all within ~40 m of each other
            p.coordinates = Coordinates(lat=42.45 + i * 0.00003, lng=18.53)
        gems = osm_places({"man_made": "adit", "name": "Stari rudnik"},
                          {"military": "bunker", "name": "Bunker Zlatica"}, start_id=100)
        gems[0].coordinates = Coordinates(lat=42.50, lng=18.60)
        gems[1].coordinates = Coordinates(lat=42.40, lng=18.45)
        sources["osm"].search.return_value = places + gems
        result = await discover(DiscoverRequest(**REQ, sort_by="unusual", limit=3))
        names = {p.name for p in result.places}
        assert {"Stari rudnik", "Bunker Zlatica"} <= names
        assert len([n for n in names if n.startswith("Barracks")]) == 1

    async def test_pagination_with_unusual_sort_is_stable(self, sources):
        sources["osm"].search.return_value = osm_places(
            *[{"historic": "ruins", "name": f"Ruin Number{i}"} for i in range(9)])
        first = await discover(DiscoverRequest(**REQ, sort_by="unusual", limit=4))
        second = await discover(DiscoverRequest(**REQ, sort_by="unusual", limit=4, cursor=first.cursor))
        third = await discover(DiscoverRequest(**REQ, sort_by="unusual", limit=4, cursor=second.cursor))
        ids = [p.id for r in (first, second, third) for p in r.places]
        assert len(ids) == 9 and len(set(ids)) == 9
        assert first.has_more and second.has_more and not third.has_more

    async def test_fused_place_is_scored_on_all_its_evidence(self, sources):
        sources["osm"].search.return_value = osm_places(
            {"military": "bunker", "name": "Fort Zlatica"})
        sources["atlas"].search.return_value = [Place(
            id="atlas_1", source=PlaceSource.ATLAS_OBSCURA, sources=[PlaceSource.ATLAS_OBSCURA],
            name="Zlatica Fort", categories=[C.MILITARY], coordinates=Coordinates(lat=42.4501, lng=18.53),
        )]
        result = await discover(DiscoverRequest(**REQ))
        assert result.total == 1
        place = result.places[0]
        assert "listed on Atlas Obscura" in place.unusual_reasons
        assert place.unusual_score > 0.75

    async def test_exclude_visited_honours_merged_ids(self, sources):
        sources["osm"].search.return_value = osm_places({"military": "bunker", "name": "Fort Zlatica"})
        sources["atlas"].search.return_value = [Place(
            id="atlas_9", source=PlaceSource.ATLAS_OBSCURA, sources=[PlaceSource.ATLAS_OBSCURA],
            name="Zlatica Fort", categories=[C.MILITARY], coordinates=Coordinates(lat=42.4501, lng=18.53),
        )]
        # whichever id ended up as the merged place's id, visiting the *other* hides it too
        for visited in ("osm_node_1", "atlas_9"):
            result = await discover(DiscoverRequest(**REQ, exclude_visited=[visited]))
            assert result.total == 0, visited


class TestSourceStatus:
    async def test_ok_empty_error(self, sources):
        sources["osm"].search.return_value = osm_places({"military": "bunker"})
        sources["atlas"].search.side_effect = RuntimeError("403")
        result = await discover(DiscoverRequest(**REQ))
        assert result.source_status == {
            "osm": "ok", "atlas": "error", "wiki": "empty", "deep_research": "empty"}
        assert result.total == 1  # a failing source never breaks the request

    async def test_timeout(self, sources, monkeypatch):
        monkeypatch.setattr(settings, "discovery_source_timeout_seconds", 0.05)

        async def slow(*_):
            await asyncio.sleep(5)

        sources["wiki"].search.side_effect = slow
        sources["osm"].search.return_value = osm_places({"military": "bunker"})
        t0 = time.monotonic()
        result = await discover(DiscoverRequest(**REQ))
        assert time.monotonic() - t0 < 2
        assert result.source_status["wiki"] == "timeout" and result.source_status["osm"] == "ok"

    async def test_all_sources_failing_still_returns_a_response(self, sources):
        for m in ("osm", "atlas", "wiki", "deep"):
            sources[m].search.side_effect = RuntimeError("down")
        result = await discover(DiscoverRequest(**REQ))
        assert result.total == 0 and result.places == []
        assert set(result.source_status.values()) == {"error"}

    async def test_sources_are_queried_concurrently(self, sources):
        async def slow_empty(*_):
            await asyncio.sleep(0.25)
            return []

        for m in ("osm", "atlas", "wiki", "deep"):
            sources[m].search.side_effect = slow_empty
        t0 = time.monotonic()
        await discover(DiscoverRequest(**REQ))
        assert time.monotonic() - t0 < 0.7  # serial would be >= 1.0 s


class TestDiscoverApi:
    def test_unusual_sort_is_accepted_and_reaches_the_pipeline(self):
        with patch("app.api.discover.discover", new_callable=AsyncMock) as mock:
            mock.return_value = DiscoverResponse(places=[], total=0, has_more=False)
            resp = TestClient(app).post("/api/discover", json={**REQ, "sort_by": "unusual"})
        assert resp.status_code == 200
        assert mock.call_args[0][0].sort_by == "unusual"

    def test_other_sort_values_remain_valid_and_garbage_is_rejected(self):
        client = TestClient(app)
        with patch("app.api.discover.discover", new_callable=AsyncMock) as mock:
            mock.return_value = DiscoverResponse(places=[], total=0, has_more=False)
            for ok in ("confidence", "distance", "name", "unusual"):
                assert client.post("/api/discover", json={**REQ, "sort_by": ok}).status_code == 200
        assert client.post("/api/discover", json={**REQ, "sort_by": "random"}).status_code == 422

    def test_response_shape_is_backward_compatible_and_additive(self):
        with patch("app.api.discover.discover", new_callable=AsyncMock) as mock:
            place = Place(id="x", source=PlaceSource.OSM, coordinates=Coordinates(lat=1, lng=2))
            mock.return_value = DiscoverResponse(places=[place], total=1, has_more=False)
            data = TestClient(app).post("/api/discover", json=REQ).json()
        assert {"places", "total", "has_more", "cursor", "deep_research_status",
                "deep_research_message", "source_status"} <= set(data)
        p = data["places"][0]
        assert {"id", "source", "sources", "name", "description", "categories", "coordinates",
                "confidence", "category_confidence", "distance_m", "tags", "photos", "metadata",
                "unusual_score", "unusual_reasons", "wikipedia"} <= set(p)
        assert p["unusual_score"] == 0.0 and p["unusual_reasons"] == [] and p["wikipedia"] is None
        assert data["source_status"] == {}


class TestDiscoverPlacesFromSources:
    async def test_returns_places_and_ignores_unknown_categories(self, sources):
        sources["osm"].search.return_value = osm_places(
            {"military": "bunker", "name": "Zlatica"}, {"tourism": "museum", "name": "Museum"})
        places = await discover_places_from_sources(42.45, 18.53, 5.0, ["military", "not-a-category"])
        assert [p.name for p in places] == ["Zlatica"]

    async def test_clamps_radius(self, sources):
        await discover_places_from_sources(42.45, 18.53, 500.0)
        assert sources["osm"].search.call_args[0][2] == settings.max_radius_km
