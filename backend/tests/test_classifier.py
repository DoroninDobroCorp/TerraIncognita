"""Tests for Story 1.5 — Category Classification."""

from __future__ import annotations

import pytest

from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.classifier import classify_place, classify_places


def _make_place(
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    categories: list[PlaceCategory] | None = None,
    metadata: dict | None = None,
) -> Place:
    return Place(
        id="test_1",
        source=PlaceSource.OSM,
        name=name,
        description=description,
        categories=categories or [],
        coordinates=Coordinates(lat=42.0, lng=18.0),
        tags=tags or [],
        metadata=metadata or {},
    )


class TestClassifier:
    def test_already_classified_unchanged(self):
        p = _make_place(categories=[PlaceCategory.MILITARY])
        p.category_confidence = {PlaceCategory.MILITARY.value: 0.9}
        result = classify_place(p)
        assert result.categories == [PlaceCategory.MILITARY]

    def test_category_confidence_populated(self):
        p = _make_place(name="Abandoned Factory")
        result = classify_place(p)
        assert "abandoned" in result.category_confidence
        assert result.category_confidence["abandoned"] > 0.0

    def test_classify_by_name_abandoned(self):
        p = _make_place(name="Abandoned Factory")
        result = classify_place(p)
        assert PlaceCategory.ABANDONED in result.categories

    def test_classify_by_description_tunnel(self):
        p = _make_place(description="An old underground tunnel beneath the city")
        result = classify_place(p)
        assert PlaceCategory.UNDERGROUND in result.categories

    def test_classify_by_tags(self):
        p = _make_place(tags=["historic=bunker", "military=bunker"])
        result = classify_place(p)
        assert PlaceCategory.MILITARY in result.categories

    def test_classify_cave(self):
        p = _make_place(name="Blue Cave")
        result = classify_place(p)
        assert PlaceCategory.CAVE in result.categories

    def test_classify_church(self):
        p = _make_place(name="St. Peter's Cathedral Church")
        result = classify_place(p)
        assert PlaceCategory.RELIGIOUS in result.categories

    def test_classify_museum(self):
        p = _make_place(name="Maritime Museum")
        result = classify_place(p)
        assert PlaceCategory.MUSEUM in result.categories

    def test_classify_monument(self):
        p = _make_place(description="A memorial to the fallen soldiers")
        result = classify_place(p)
        assert PlaceCategory.MONUMENT in result.categories

    def test_classify_waterfall(self):
        p = _make_place(name="Hidden Waterfall in the Canyon")
        result = classify_place(p)
        assert PlaceCategory.WATER in result.categories

    def test_multi_category(self):
        p = _make_place(name="Abandoned Military Bunker")
        result = classify_place(p)
        assert PlaceCategory.ABANDONED in result.categories
        assert PlaceCategory.MILITARY in result.categories

    def test_default_landmark_when_unclassifiable(self):
        p = _make_place(name="Something Unique 12345")
        result = classify_place(p)
        assert PlaceCategory.LANDMARK in result.categories

    def test_batch_classify(self):
        places = [
            _make_place(name="Old Ruins"),
            _make_place(name="Train Station"),
            _make_place(name="Secret Garden"),
        ]
        result = classify_places(places)
        assert len(result) == 3
        assert PlaceCategory.RUINS in result[0].categories
        assert PlaceCategory.TRANSPORT in result[1].categories

    def test_classify_by_osm_tags_metadata(self):
        p = _make_place(
            metadata={"osm_tags": {"tourism": "viewpoint", "name": "Hilltop"}}
        )
        result = classify_place(p)
        assert PlaceCategory.VIEWPOINT in result.categories
