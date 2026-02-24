"""Tests for Story 1.4 — Data Fusion & Deduplication."""

from __future__ import annotations

import pytest

from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.fusion import fuse_places, _compute_confidence


def _make_place(
    id: str,
    lat: float = 42.45,
    lng: float = 18.53,
    source: PlaceSource = PlaceSource.OSM,
    name: str | None = None,
    description: str | None = None,
    categories: list[PlaceCategory] | None = None,
    photos: list[str] | None = None,
) -> Place:
    return Place(
        id=id,
        source=source,
        sources=[source],
        name=name,
        description=description,
        categories=categories or [],
        coordinates=Coordinates(lat=lat, lng=lng),
        photos=photos or [],
    )


class TestFusion:
    def test_empty_input(self):
        assert fuse_places([]) == []
        assert fuse_places([[], []]) == []

    def test_no_dedup_when_far_apart(self):
        p1 = _make_place("a", lat=42.45, lng=18.53)
        p2 = _make_place("b", lat=43.00, lng=19.00)  # ~70km away
        result = fuse_places([[p1], [p2]])
        assert len(result) == 2

    def test_dedup_when_close(self):
        p1 = _make_place("osm_1", lat=42.450000, lng=18.530000, source=PlaceSource.OSM,
                          name="Bunker")
        p2 = _make_place("atlas_1", lat=42.450001, lng=18.530001, source=PlaceSource.ATLAS_OBSCURA,
                          name="Old Bunker", description="A WWII bunker")
        result = fuse_places([[p1], [p2]], dedup_distance_m=50)
        assert len(result) == 1
        merged = result[0]
        # Should pick the longer name / description
        assert merged.name == "Old Bunker"
        assert merged.description == "A WWII bunker"
        # Both sources present
        assert PlaceSource.OSM in merged.sources
        assert PlaceSource.ATLAS_OBSCURA in merged.sources

    def test_categories_merged(self):
        p1 = _make_place("a", categories=[PlaceCategory.MILITARY])
        p2 = _make_place("b", lat=42.450001, lng=18.530001,
                          categories=[PlaceCategory.UNDERGROUND])
        result = fuse_places([[p1, p2]], dedup_distance_m=50)
        assert len(result) == 1
        assert PlaceCategory.MILITARY in result[0].categories
        assert PlaceCategory.UNDERGROUND in result[0].categories

    def test_photos_deduped(self):
        p1 = _make_place("a", photos=["url1", "url2"])
        p2 = _make_place("b", lat=42.450001, lng=18.530001, photos=["url2", "url3"])
        result = fuse_places([[p1, p2]], dedup_distance_m=50)
        assert result[0].photos == ["url1", "url2", "url3"]

    def test_confidence_multi_source(self):
        p = _make_place("a", name="X", description="Y", photos=["url"])
        conf = _compute_confidence(p, source_count=3)
        # source: 0.3*(3/3) = 0.3, photos: 0.3*1 = 0.3, desc: 0.2*1 = 0.2, fresh: 0.2*0.8 = 0.16
        assert conf == pytest.approx(0.96, abs=0.01)

    def test_confidence_single_source_no_data(self):
        p = _make_place("a")
        conf = _compute_confidence(p, source_count=1)
        # source: 0.3*(1/3) = 0.1, photos: 0, desc: 0, fresh: 0.16
        assert conf == pytest.approx(0.26, abs=0.01)

    def test_single_place_not_duplicated(self):
        p1 = _make_place("only_one")
        result = fuse_places([[p1]])
        assert len(result) == 1
        assert result[0].id == "only_one"

    def test_three_sources_merge(self):
        p1 = _make_place("osm_1", source=PlaceSource.OSM, name="Fort")
        p2 = _make_place("atlas_1", lat=42.450001, lng=18.530001,
                          source=PlaceSource.ATLAS_OBSCURA, description="Old fort")
        p3 = _make_place("wiki_1", lat=42.450002, lng=18.530002,
                          source=PlaceSource.WIKIDATA, photos=["img.jpg"])
        result = fuse_places([[p1], [p2], [p3]], dedup_distance_m=50)
        assert len(result) == 1
        assert len(result[0].sources) == 3
