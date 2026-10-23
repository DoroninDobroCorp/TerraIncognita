"""Tests for OSM tag interpretation (categories, lifecycle, generated names, links, photos)."""

from __future__ import annotations

import pytest

from app.models.place import PlaceCategory as C
from app.sources.osm_tags import (
    commons_category,
    describe_date,
    effective_tags,
    extract_photos,
    interpret_tags,
    is_street_not_place,
    meaningful_tag_list,
    parse_wikidata_id,
    parse_wikipedia_tag,
    parse_year,
    pick_description,
    pick_name,
    wikipedia_url,
)

# (tags, expected categories (subset), lifecycle, generated name)
_INTERPRET_CASES = [
    pytest.param({"military": "bunker"}, {C.MILITARY, C.UNDERGROUND}, None, "Bunker",
                 id="bunker"),
    pytest.param({"military": "bunker", "abandoned": "yes"}, {C.MILITARY, C.ABANDONED},
                 "abandoned", "Abandoned bunker", id="abandoned-bunker"),
    pytest.param({"building": "bunker", "disused": "yes", "military": "bunker"},
                 {C.MILITARY, C.ABANDONED}, "disused", "Disused bunker", id="disused-bunker"),
    pytest.param({"abandoned": "yes", "building": "yes"}, {C.ABANDONED}, "abandoned",
                 "Abandoned building", id="abandoned-building"),
    pytest.param({"abandoned": "yes", "building": "house"}, {C.ABANDONED}, "abandoned",
                 "Abandoned house", id="abandoned-house"),
    pytest.param({"abandoned": "yes", "building": "church"}, {C.ABANDONED, C.RELIGIOUS},
                 "abandoned", "Abandoned church", id="abandoned-church"),
    pytest.param({"abandoned": "yes", "building": "yes", "military": "barracks"},
                 {C.ABANDONED, C.MILITARY}, "abandoned", "Abandoned barracks",
                 id="abandoned-barracks"),
    pytest.param({"abandoned:railway": "station", "railway": "site", "name": "Igalo"},
                 {C.ABANDONED, C.TRANSPORT}, "abandoned", "Abandoned railway station",
                 id="abandoned-station-prefix-beats-railway-site"),
    pytest.param({"disused:railway": "halt"}, {C.ABANDONED, C.TRANSPORT}, "disused",
                 "Disused railway halt", id="disused-halt"),
    pytest.param({"abandoned:building": "yes", "building": "yes"}, {C.ABANDONED}, "abandoned",
                 "Abandoned building", id="abandoned-prefix-building"),
    pytest.param({"abandoned:amenity": "hospital", "building": "yes"}, {C.ABANDONED}, "abandoned",
                 "Abandoned hospital", id="abandoned-hospital"),
    pytest.param({"railway": "abandoned", "name": "Dalmatinerbahn"}, {C.ABANDONED, C.TRANSPORT},
                 "abandoned", "Abandoned railway line", id="abandoned-railway"),
    pytest.param({"railway": "disused", "tunnel": "yes"}, {C.ABANDONED, C.UNDERGROUND},
                 "disused", "Disused railway line", id="disused-railway-tunnel"),
    pytest.param({"historic": "ruins"}, {C.RUINS}, "ruins", "Ruins", id="historic-ruins"),
    pytest.param({"ruins": "yes", "building": "yes"}, {C.RUINS}, "ruins", "Ruined building",
                 id="ruins-yes-building"),
    pytest.param({"building": "ruins"}, {C.RUINS}, "ruins", "Ruins", id="building-ruins"),
    pytest.param({"building": "collapsed"}, {C.RUINS}, "ruins", "Ruins", id="building-collapsed"),
    pytest.param({"ruined": "yes", "building": "yes", "abandoned": "yes"}, {C.RUINS, C.ABANDONED},
                 "ruins", "Ruined building", id="ruined-tag"),
    pytest.param({"ruins:building": "church"}, {C.RUINS, C.RELIGIOUS}, "ruins", "Ruined church",
                 id="ruined-church-prefix"),
    pytest.param({"historic": "castle", "ruins": "yes"}, {C.RUINS, C.LANDMARK}, "ruins",
                 "Ruined castle", id="ruined-castle"),
    pytest.param({"historic": "fort"}, {C.LANDMARK, C.MILITARY}, None, "Fort", id="fort"),
    pytest.param({"historic": "archaeological_site"}, {C.RUINS}, None, "Archaeological site",
                 id="archaeological-site"),
    pytest.param({"man_made": "adit", "natural": "cave_entrance"},
                 {C.UNDERGROUND, C.INDUSTRIAL, C.CAVE}, None, "Mine adit", id="adit"),
    pytest.param({"natural": "cave_entrance"}, {C.CAVE, C.UNDERGROUND}, None, "Cave entrance",
                 id="cave"),
    pytest.param({"man_made": "tunnel"}, {C.UNDERGROUND}, None, "Tunnel", id="tunnel"),
    pytest.param({"tunnel": "yes", "highway": "footway"}, {C.UNDERGROUND}, None, None,
                 id="footway-tunnel-no-feature"),
    pytest.param({"tunnel": "culvert", "waterway": "stream"}, set(), None, None, id="culvert"),
    pytest.param({"man_made": "water_tower"}, {C.INDUSTRIAL, C.ARCHITECTURE}, None, "Water tower",
                 id="water-tower"),
    pytest.param({"man_made": "silo"}, {C.INDUSTRIAL}, None, "Silo", id="silo"),
    pytest.param({"man_made": "lighthouse"}, {C.LANDMARK, C.ARCHITECTURE}, None, "Lighthouse",
                 id="lighthouse"),
    pytest.param({"man_made": "tower", "tower:type": "cooling"}, {C.INDUSTRIAL}, None, "Tower",
                 id="cooling-tower"),
    pytest.param({"man_made": "tower", "tower:type": "defensive"}, {C.MILITARY}, None, "Tower",
                 id="defensive-tower"),
    pytest.param({"industrial": "brewery", "abandoned": "yes"}, {C.INDUSTRIAL, C.ABANDONED},
                 "abandoned", "Abandoned brewery", id="abandoned-brewery"),
    pytest.param({"historic": "wreck"}, {C.UNDERWATER}, None, "Shipwreck", id="wreck"),
    pytest.param({"historic": "mine"}, {C.INDUSTRIAL, C.UNDERGROUND}, None, "Old mine",
                 id="historic-mine"),
    pytest.param({"military": "airfield", "abandoned": "yes"}, {C.MILITARY, C.ABANDONED},
                 "abandoned", "Abandoned military airfield", id="abandoned-airfield"),
    pytest.param({"tourism": "museum"}, {C.MUSEUM}, None, "Museum", id="museum"),
    pytest.param({"tourism": "viewpoint"}, {C.VIEWPOINT}, None, "Viewpoint", id="viewpoint"),
    pytest.param({"tourism": "artwork", "artwork_type": "mural"}, {C.STREET_ART}, None, "Artwork",
                 id="mural"),
    pytest.param({"tourism": "artwork", "artwork_type": "statue"}, {C.MONUMENT}, None, "Artwork",
                 id="statue-is-not-street-art"),
    pytest.param({"historic": "memorial", "name": "x"}, {C.MONUMENT}, None, "Memorial",
                 id="memorial"),
    pytest.param({"building": "cathedral"}, {C.RELIGIOUS, C.ARCHITECTURE}, None, "Cathedral",
                 id="cathedral"),
    pytest.param({"abandoned": "yes", "man_made": "pier"}, {C.ABANDONED}, "abandoned",
                 "Abandoned pier", id="abandoned-pier"),
    pytest.param({"abandoned": "yes", "landuse": "farmland"}, {C.ABANDONED}, "abandoned",
                 "Abandoned place", id="abandoned-unknown-thing-is-honest"),
    pytest.param({"abandoned": "no", "building": "yes"}, set(), None, "Building",
                 id="abandoned-no-is-not-abandoned"),
]


@pytest.mark.parametrize(("tags", "expected_cats", "lifecycle", "gen_name"), _INTERPRET_CASES)
def test_interpret_tags(tags, expected_cats, lifecycle, gen_name):
    info = interpret_tags(tags)
    assert expected_cats <= set(info.categories)
    assert info.lifecycle == lifecycle
    if gen_name is not None:
        assert info.generated_name == gen_name


def test_notable_restaurants_and_hotels_need_a_marker():
    plain = interpret_tags({"amenity": "restaurant", "name": "Pizza"})
    assert C.RESTAURANT_NOTABLE not in plain.categories
    assert C.RESTAURANT_NOTABLE in interpret_tags(
        {"amenity": "restaurant", "heritage": "2"}).categories
    assert C.HOTEL_NOTABLE not in interpret_tags({"tourism": "hotel"}).categories
    assert C.HOTEL_NOTABLE in interpret_tags({"tourism": "hotel", "stars": "5"}).categories


def test_categories_are_sorted_and_unique():
    info = interpret_tags({"military": "bunker", "historic": "bunker", "abandoned": "yes"})
    values = [c.value for c in info.categories]
    assert values == sorted(set(values))


def test_effective_tags_resolves_prefix_without_clobbering_real_tags():
    eff, states = effective_tags({"abandoned:railway": "station", "railway": "site"})
    assert eff["railway"] == "station"  # the prefixed value says more than `site`
    assert states == ["abandoned"]
    eff, _ = effective_tags({"disused:shop": "bakery", "shop": "bakery"})
    assert eff["shop"] == "bakery"


def test_lifecycle_priority_ruins_over_abandoned_over_disused():
    assert interpret_tags({"abandoned": "yes", "disused": "yes"}).lifecycle == "abandoned"
    assert interpret_tags({"abandoned": "yes", "ruins": "yes"}).lifecycle == "ruins"


@pytest.mark.parametrize(("tags", "expected"), [
    ({"name": "Fort", "name:en": "Fort EN"}, "Fort"),
    ({"name:en": "Fort EN", "name:ru": "Форт"}, "Fort EN"),
    ({"name:ru": "Форт"}, "Форт"),
    ({"name:sr": "Тврђава", "name:de": "Festung"}, "Festung"),  # alphabetical among languages
    ({"alt_name": "Alt;Other"}, "Alt"),
    ({"name:signed": "no", "name:etymology": "someone"}, None),  # not language variants
    ({"name": "   "}, None),
    ({}, None),
])
def test_pick_name(tags, expected):
    assert pick_name(tags) == expected


def test_pick_description_prefers_description_over_note():
    assert pick_description({"note": "n", "description": "d"}) == "d"
    assert pick_description({"note": "n"}) == "n"
    assert pick_description({"description:en": "english"}) == "english"
    assert pick_description({}) is None


def test_wikipedia_and_wikidata_tags():
    assert parse_wikipedia_tag("en:Fort Mamula") == ("en", "Fort Mamula")
    assert parse_wikipedia_tag("sr:Манастир Савина") == ("sr", "Манастир Савина")
    assert parse_wikipedia_tag("Fort Mamula") is None  # no language prefix
    assert parse_wikipedia_tag("xx-yy:") is None
    assert wikipedia_url("en", "Fort Mamula") == "https://en.wikipedia.org/wiki/Fort_Mamula"
    assert "%" in wikipedia_url("sr", "Манастир Савина")
    assert parse_wikidata_id("Q260715") == "Q260715"
    assert parse_wikidata_id("Q1;Q2") == "Q1"
    assert parse_wikidata_id("foo") is None
    assert parse_wikidata_id("Q0") is None
    assert parse_wikidata_id(None) is None


@pytest.mark.parametrize(("tags", "expected"), [
    ({"wikimedia_commons": "File:Mamula Island.jpg"},
     ["https://commons.wikimedia.org/wiki/Special:FilePath/Mamula_Island.jpg?width=640"]),
    ({"image": "https://commons.wikimedia.org/wiki/File:Kanli_Kula.jpg"},
     ["https://commons.wikimedia.org/wiki/Special:FilePath/Kanli_Kula.jpg?width=640"]),
    ({"image": "File:Nice (1).jpg"},
     ["https://commons.wikimedia.org/wiki/Special:FilePath/Nice_%281%29.jpg?width=640"]),
    ({"image": "http://example.com/a.jpg"}, ["https://example.com/a.jpg"]),
    ({"image": "https://example.com/a.jpg;https://example.com/b.jpg"},
     ["https://example.com/a.jpg", "https://example.com/b.jpg"]),
    ({"wikimedia_commons": "Category:Fort Mamula"}, []),  # a gallery, not a photo
    ({"image": "https://example.com/gallery.html"}, []),
    ({"image": "javascript:alert(1)"}, []),
    ({"image": "ftp://example.com/a.jpg"}, []),
    ({"wikimedia_commons": "File:A.jpg", "image": "File:A.jpg"},
     ["https://commons.wikimedia.org/wiki/Special:FilePath/A.jpg?width=640"]),  # no duplicates
    ({}, []),
])
def test_extract_photos(tags, expected):
    assert extract_photos(tags) == expected


def test_commons_category():
    assert commons_category({"wikimedia_commons": "Category:Fort Mamula"}) == "Category:Fort Mamula"
    assert commons_category({"wikimedia_commons": "File:A.jpg"}) is None


@pytest.mark.parametrize(("value", "year"), [
    ("1914", 1914), ("1914-06-28", 1914), ("~1890s", 1890), ("1900..1910", 1900),
    ("before 1800", 1800), ("C19", 1850), ("19th century", 1850), ("early 20th century", 1950),
    ("0105", 105), ("abc", None), ("", None), (None, None), ("9999", None),
])
def test_parse_year(value, year):
    assert parse_year(value) == year


def test_street_that_used_to_be_a_railway_is_not_a_place():
    assert is_street_not_place({"railway": "abandoned", "highway": "residential", "name": "X"})
    assert not is_street_not_place({"railway": "abandoned", "highway": "footway"})
    assert not is_street_not_place({"railway": "abandoned", "highway": "tertiary", "tunnel": "yes"})
    assert not is_street_not_place({"railway": "abandoned"})
    assert not is_street_not_place({"highway": "residential"})


def test_meaningful_tag_list_drops_noise():
    tags = {
        "military": "bunker", "abandoned": "yes", "name": "Old Bunker", "name:en": "x",
        "addr:street": "Park Avenue", "source": "survey", "source:geometry": "bing",
        "wikidata": "Q1", "image": "https://x/y.jpg", "building": "bunker", "note": "check",
        "access": "no",
    }
    assert sorted(meaningful_tag_list(tags)) == ["abandoned=yes", "access=no", "military=bunker"]


class TestDescribeDate:
    @pytest.mark.parametrize("raw, expected", [
        ("1914-06", "dates from 1914"),
        ("1650", "dates from 1650"),
        ("C19", "dates from the 19th century"),
        ("19th century", "dates from the 19th century"),
        ("C1", "dates from the 1st century"),
        ("after 1900", "dates from after 1900"),
        (">1900", "dates from after 1900"),
        ("C12", "dates from the 12th century"),
        ("C22", "dates from the 22nd century"),
        ("before 1800", "dates from before 1800"),
        ("<1800", "dates from before 1800"),
        ("1900..1910", "dates from 1900"),
    ])
    def test_wording_matches_the_precision_of_the_data(self, raw, expected):
        assert describe_date(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", None, "unknown", "yes", "C0", "0th century"])
    def test_nothing_to_say_without_a_date(self, raw):
        assert describe_date(raw) is None


@pytest.mark.parametrize("tags", [
    {"tunnel": "yes", "highway": "primary", "name": "Tunel Sozina"},
    {"tunnel": "yes", "highway": "residential"},
    {"tunnel": "yes", "railway": "rail"},
    {"tunnel": "yes", "railway": "subway"},
    {"tunnel": "yes", "waterway": "canal"},
])
def test_a_road_rail_or_canal_running_underground_is_not_an_underground_place(tags):
    assert C.UNDERGROUND not in interpret_tags(tags).categories


@pytest.mark.parametrize("tags", [
    {"tunnel": "yes", "tunnel:name": "Stari tunel"},
    {"tunnel": "yes", "highway": "footway", "name": "Pjesacki tunel"},
    {"tunnel": "yes", "railway": "abandoned"},
    {"tunnel": "yes", "railway": "disused"},
])
def test_a_named_pedestrian_or_dead_tunnel_still_is(tags):
    assert C.UNDERGROUND in interpret_tags(tags).categories
