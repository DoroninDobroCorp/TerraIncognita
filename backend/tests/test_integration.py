"""Integration test for the full discovery pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.place import (
    Coordinates,
    DiscoverRequest,
    Place,
    PlaceCategory,
    PlaceSource,
)
from app.services.discovery import discover


def _osm_places():
    return [
        Place(
            id="osm_node_1",
            source=PlaceSource.OSM,
            sources=[PlaceSource.OSM],
            name="Old Bunker",
            categories=[PlaceCategory.MILITARY],
            coordinates=Coordinates(lat=42.45, lng=18.53),
            confidence=0.7,
            tags=["military=bunker"],
        ),
        Place(
            id="osm_node_2",
            source=PlaceSource.OSM,
            sources=[PlaceSource.OSM],
            name="Viewpoint Hill",
            categories=[PlaceCategory.VIEWPOINT],
            coordinates=Coordinates(lat=42.46, lng=18.54),
            confidence=0.6,
        ),
    ]


def _atlas_places():
    return [
        Place(
            id="atlas_1",
            source=PlaceSource.ATLAS_OBSCURA,
            sources=[PlaceSource.ATLAS_OBSCURA],
            name="The Bunker Complex",
            description="A massive WWII bunker complex",
            categories=[PlaceCategory.MILITARY, PlaceCategory.ABANDONED],
            coordinates=Coordinates(lat=42.450001, lng=18.530001),  # ~0.1m from osm_node_1
            confidence=0.85,
            photos=["photo.jpg"],
        ),
    ]


def _wiki_places():
    return [
        Place(
            id="wiki_Q999",
            source=PlaceSource.WIKIDATA,
            sources=[PlaceSource.WIKIDATA],
            name="Ancient Fortress",
            description="Medieval fortress ruins",
            categories=[PlaceCategory.RUINS, PlaceCategory.LANDMARK],
            coordinates=Coordinates(lat=42.47, lng=18.55),
            confidence=0.7,
        ),
    ]


@pytest.mark.asyncio
@patch("app.services.discovery._wiki")
@patch("app.services.discovery._atlas")
@patch("app.services.discovery._osm")
async def test_full_pipeline(mock_osm, mock_atlas, mock_wiki):
    mock_osm.search = AsyncMock(return_value=_osm_places())
    mock_osm.source_name = "osm"
    mock_atlas.search = AsyncMock(return_value=_atlas_places())
    mock_atlas.source_name = "atlas"
    mock_wiki.search = AsyncMock(return_value=_wiki_places())
    mock_wiki.source_name = "wiki"

    req = DiscoverRequest(lat=42.45, lng=18.53, radius_km=5.0)
    result = await discover(req)

    # osm_node_1 and atlas_1 should be deduplicated (within 50m)
    # Remaining: merged(osm_node_1 + atlas_1), osm_node_2, wiki_Q999
    assert result.total == 3

    # The merged place should have data from both sources
    merged = [p for p in result.places if PlaceCategory.MILITARY in p.categories]
    assert len(merged) >= 1
    m = merged[0]
    assert PlaceSource.OSM in m.sources or PlaceSource.ATLAS_OBSCURA in m.sources


@pytest.mark.asyncio
@patch("app.services.discovery._wiki")
@patch("app.services.discovery._atlas")
@patch("app.services.discovery._osm")
async def test_filter_by_category(mock_osm, mock_atlas, mock_wiki):
    mock_osm.search = AsyncMock(return_value=_osm_places())
    mock_osm.source_name = "osm"
    mock_atlas.search = AsyncMock(return_value=[])
    mock_atlas.source_name = "atlas"
    mock_wiki.search = AsyncMock(return_value=_wiki_places())
    mock_wiki.source_name = "wiki"

    req = DiscoverRequest(
        lat=42.45, lng=18.53, radius_km=5.0,
        categories=[PlaceCategory.VIEWPOINT],
    )
    result = await discover(req)
    assert result.total == 1
    assert result.places[0].categories == [PlaceCategory.VIEWPOINT]


@pytest.mark.asyncio
@patch("app.services.discovery._wiki")
@patch("app.services.discovery._atlas")
@patch("app.services.discovery._osm")
async def test_exclude_visited(mock_osm, mock_atlas, mock_wiki):
    mock_osm.search = AsyncMock(return_value=_osm_places())
    mock_osm.source_name = "osm"
    mock_atlas.search = AsyncMock(return_value=[])
    mock_atlas.source_name = "atlas"
    mock_wiki.search = AsyncMock(return_value=[])
    mock_wiki.source_name = "wiki"

    req = DiscoverRequest(
        lat=42.45, lng=18.53, radius_km=5.0,
        exclude_visited=["osm_node_1"],
    )
    result = await discover(req)
    assert all(p.id != "osm_node_1" for p in result.places)


@pytest.mark.asyncio
@patch("app.services.discovery._wiki")
@patch("app.services.discovery._atlas")
@patch("app.services.discovery._osm")
async def test_sort_by_distance(mock_osm, mock_atlas, mock_wiki):
    mock_osm.search = AsyncMock(return_value=_osm_places())
    mock_osm.source_name = "osm"
    mock_atlas.search = AsyncMock(return_value=[])
    mock_atlas.source_name = "atlas"
    mock_wiki.search = AsyncMock(return_value=_wiki_places())
    mock_wiki.source_name = "wiki"

    req = DiscoverRequest(
        lat=42.45, lng=18.53, radius_km=10.0,
        sort_by="distance",
    )
    result = await discover(req)
    # First place should be closest to request point
    assert result.total >= 2


@pytest.mark.asyncio
@patch("app.services.discovery._wiki")
@patch("app.services.discovery._atlas")
@patch("app.services.discovery._osm")
async def test_source_failure_isolated(mock_osm, mock_atlas, mock_wiki):
    mock_osm.search = AsyncMock(side_effect=RuntimeError("OSM down"))
    mock_osm.source_name = "osm"
    mock_atlas.search = AsyncMock(return_value=_atlas_places())
    mock_atlas.source_name = "atlas"
    mock_wiki.search = AsyncMock(return_value=_wiki_places())
    mock_wiki.source_name = "wiki"

    req = DiscoverRequest(lat=42.45, lng=18.53, radius_km=5.0)
    result = await discover(req)
    # Should still return results from working sources
    assert result.total >= 1


@pytest.mark.asyncio
@patch("app.services.discovery._wiki")
@patch("app.services.discovery._atlas")
@patch("app.services.discovery._osm")
async def test_pagination(mock_osm, mock_atlas, mock_wiki):
    many_places = [
        Place(
            id=f"osm_{i}",
            source=PlaceSource.OSM,
            name=f"Place {i}",
            categories=[PlaceCategory.LANDMARK],
            coordinates=Coordinates(lat=42.45 + i * 0.001, lng=18.53),
            confidence=0.5,
        )
        for i in range(10)
    ]
    mock_osm.search = AsyncMock(return_value=many_places)
    mock_osm.source_name = "osm"
    mock_atlas.search = AsyncMock(return_value=[])
    mock_atlas.source_name = "atlas"
    mock_wiki.search = AsyncMock(return_value=[])
    mock_wiki.source_name = "wiki"

    req = DiscoverRequest(lat=42.45, lng=18.53, radius_km=5.0, limit=3)
    result = await discover(req)
    assert len(result.places) == 3
    assert result.total == 10
    assert result.has_more is True
    assert result.cursor is not None

    # Second page
    req2 = DiscoverRequest(lat=42.45, lng=18.53, radius_km=5.0, limit=3, cursor=result.cursor)
    result2 = await discover(req2)
    assert len(result2.places) == 3
    assert result2.has_more is True
