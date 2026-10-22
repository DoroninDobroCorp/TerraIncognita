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


# ── Word-based matching & multilingual stems ─────────────────────────────────


@pytest.mark.parametrize(("text", "not_expected"), [
    ("Parking Lot", PlaceCategory.PARK),
    ("Adam's Corner", PlaceCategory.WATER),          # "dam"
    ("Research Center", PlaceCategory.ARCHITECTURE),  # "arch"
    ("Mountain Trail", PlaceCategory.TRANSPORT),      # "rail"
    ("Metropolitan Tower Hotel", PlaceCategory.UNDERGROUND),  # "metro"
    ("Plantation House", PlaceCategory.INDUSTRIAL),   # "plant"
    ("Springfield Road", PlaceCategory.WATER),        # "spring"
    ("Millennium Square", PlaceCategory.INDUSTRIAL),  # "mill"
    ("Animal Shelter", PlaceCategory.MILITARY),       # "shelter" alone is not a bunker
    ("Bus Shelter", PlaceCategory.MILITARY),
    ("Gateway Plaza", PlaceCategory.ARCHITECTURE),    # "gate"
    ("Mineral Water Spa", PlaceCategory.UNDERGROUND),  # "mine"
])
def test_keywords_match_whole_words_not_substrings(text, not_expected):
    result = classify_place(_make_place(name=text))
    assert not_expected not in result.categories


@pytest.mark.parametrize(("text", "expected"), [
    ("Заброшенный завод", {PlaceCategory.ABANDONED, PlaceCategory.INDUSTRIAL}),
    ("Старый бункер", {PlaceCategory.MILITARY}),
    ("Пещера Эмине-Баир-Хосар", {PlaceCategory.CAVE, PlaceCategory.UNDERGROUND}),
    ("Руины монастыря", {PlaceCategory.RUINS, PlaceCategory.RELIGIOUS}),
    ("Підземний тунель", {PlaceCategory.UNDERGROUND}),
    ("Покинута шахта", {PlaceCategory.ABANDONED, PlaceCategory.UNDERGROUND}),
    ("Тврђава Мамула", {PlaceCategory.LANDMARK, PlaceCategory.ARCHITECTURE}),
    ("Napuštena tvrđava", {PlaceCategory.ABANDONED, PlaceCategory.LANDMARK}),
    ("Verlassene Fabrik Ruine", {PlaceCategory.ABANDONED, PlaceCategory.RUINS}),
    ("Forte Arza", set()),  # unknown word: default landmark, no false category
])
def test_multilingual_stems(text, expected):
    result = classify_place(_make_place(name=text))
    assert expected <= set(result.categories)


def test_plural_and_inflected_forms_match():
    assert PlaceCategory.CAVE in classify_place(_make_place(name="Caves of Kotor")).categories
    assert PlaceCategory.RUINS in classify_place(_make_place(name="Ruined chapel")).categories
    assert PlaceCategory.ABANDONED in classify_place(_make_place(name="Abandonment Villa")).categories


def test_fort_is_a_landmark_and_military_but_not_fortunate():
    assert {PlaceCategory.LANDMARK, PlaceCategory.MILITARY} <= set(
        classify_place(_make_place(name="Fort Arza")).categories)
    assert PlaceCategory.MILITARY not in classify_place(_make_place(name="Fortunato Bar")).categories


def test_address_and_name_tags_do_not_pollute_classification():
    p = _make_place(
        name="Quiet Corner",
        tags=["addr:street=Park Avenue", "source=bing tunnel survey", "military=bunker"],
        metadata={"osm_tags": {"addr:street": "Cave Street", "name": "Station Cafe",
                               "historic": "ruins"}},
    )
    cats = set(classify_place(p).categories)
    assert PlaceCategory.MILITARY in cats and PlaceCategory.RUINS in cats
    assert not cats & {PlaceCategory.PARK, PlaceCategory.CAVE, PlaceCategory.TRANSPORT,
                       PlaceCategory.UNDERGROUND}


def test_lifecycle_tags_are_classified():
    p = _make_place(metadata={"osm_tags": {"abandoned:building": "factory"}})
    cats = set(classify_place(p).categories)
    assert {PlaceCategory.ABANDONED, PlaceCategory.INDUSTRIAL} <= cats


def test_places_with_explicit_category_confidence_are_left_alone():
    p = _make_place(name="Abandoned Cave Mine", categories=[PlaceCategory.MONUMENT])
    p.category_confidence = {PlaceCategory.MONUMENT.value: 0.85}
    assert classify_place(p).categories == [PlaceCategory.MONUMENT]


def test_unclassifiable_place_is_flagged_for_llm():
    p = classify_place(_make_place(name="Zorblax"))
    assert p.metadata["_needs_llm_classification"] is True
    assert p.category_confidence == {"landmark": 0.3}
