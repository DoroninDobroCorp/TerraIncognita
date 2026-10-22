"""Tests for Story 1.1 — OSM/Overpass source."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app.config import settings
from app.models.place import Place, PlaceCategory, PlaceSource
from app.services.cache import DiskCache
from app.sources.osm import (
    _GEM_CLAUSES,
    _LANDMARK_CLAUSES,
    OSMSource,
    OverpassError,
    build_overpass_query,
)
from app.utils import http_client
from app.utils.http_client import close_http_client
from app.utils.rate_limiter import RateLimiter

# Sample Overpass response
_OVERPASS_RESPONSE = {
    "elements": [
        {
            "type": "node",
            "id": 12345,
            "lat": 42.45,
            "lon": 18.53,
            "tags": {
                "name": "Old Bunker",
                "military": "bunker",
                "historic": "bunker",
                "abandoned": "yes",
            },
        },
        {
            "type": "way",
            "id": 67890,
            "center": {"lat": 42.44, "lon": 18.52},
            "tags": {
                "name": "Fortress Ruins",
                "historic": "ruins",
            },
        },
        {
            "type": "node",
            "id": 11111,
            "lat": 42.43,
            "lon": 18.51,
            "tags": {
                "name": "St. Nicholas Cathedral",
                "building": "cathedral",
            },
        },
        {
            "type": "node",
            "id": 22222,
            "lat": 42.46,
            "lon": 18.54,
            "tags": {"name": "Viewpoint"},  # no matching tags → empty categories
        },
    ],
}


class TestOSMParsing:
    def setup_method(self):
        self.source = OSMSource()

    def test_parse_node_with_coords(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][0]])
        assert len(places) == 1
        p = places[0]
        assert p.id == "osm_node_12345"
        assert p.source == PlaceSource.OSM
        assert p.name == "Old Bunker"
        assert p.coordinates.lat == 42.45
        assert p.coordinates.lng == 18.53

    def test_parse_way_with_center(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][1]])
        assert len(places) == 1
        assert places[0].coordinates.lat == 42.44

    def test_category_classification_military(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][0]])
        cats = places[0].categories
        assert PlaceCategory.MILITARY in cats
        assert PlaceCategory.ABANDONED in cats

    def test_category_classification_ruins(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][1]])
        assert PlaceCategory.RUINS in places[0].categories

    def test_category_classification_cathedral(self):
        # cathedral is a classic landmark
        places_all = self.source._parse_elements(_OVERPASS_RESPONSE["elements"])
        cathedral = [p for p in places_all if "Cathedral" in (p.name or "")]
        assert len(cathedral) == 1
        assert PlaceCategory.RELIGIOUS in cathedral[0].categories

    def test_skip_element_without_coords(self):
        el = {"type": "relation", "id": 999, "tags": {"name": "X"}}
        places = self.source._parse_elements([el])
        assert len(places) == 0

    def test_all_elements_parsed(self):
        places = self.source._parse_elements(_OVERPASS_RESPONSE["elements"])
        assert len(places) == 4

    def test_confidence_with_categories(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][0]])
        assert places[0].confidence == 0.7

    def test_confidence_without_categories(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][3]])
        assert places[0].confidence == 0.4

    def test_tags_extraction(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][0]])
        tags = places[0].tags
        assert any("military" in t for t in tags)


# ── Extended parsing (names, metadata, photos, links) ────────────────────────


def _parse_one(tags: dict, **element) -> Place:
    el = {"type": "node", "id": 1, "lat": 42.4, "lon": 18.5, "tags": tags, **element}
    return OSMSource()._parse_elements([el])[0]


class TestGeneratedNames:
    def test_unnamed_abandoned_building_gets_honest_name(self):
        p = _parse_one({"abandoned": "yes", "building": "yes"})
        assert p.name == "Abandoned building"
        assert p.metadata["name_generated"] is True
        assert p.metadata["lifecycle"] == "abandoned"
        assert PlaceCategory.ABANDONED in p.categories

    def test_unnamed_bunker(self):
        p = _parse_one({"military": "bunker"})
        assert p.name == "Bunker"
        assert p.metadata["name_generated"] is True

    def test_real_name_is_never_replaced(self):
        p = _parse_one({"military": "bunker", "name": "Zlatica"})
        assert p.name == "Zlatica"
        assert "name_generated" not in p.metadata

    def test_unnamed_ranks_below_named(self):
        named = _parse_one({"military": "bunker", "name": "Zlatica"})
        unnamed = _parse_one({"military": "bunker"})
        assert unnamed.confidence < named.confidence
        assert unnamed.confidence == pytest.approx(0.6)

    def test_untagged_element_is_skipped(self):
        assert OSMSource()._parse_elements([{"type": "node", "id": 5, "lat": 1, "lon": 1}]) == []

    def test_unknown_tags_without_name_stay_nameless(self):
        p = _parse_one({"foo": "bar"})
        assert p.name is None and p.categories == []

    def test_street_that_used_to_be_a_railway_is_dropped(self):
        el = {"type": "way", "id": 9, "center": {"lat": 42.4, "lon": 18.5},
              "tags": {"railway": "abandoned", "highway": "residential", "name": "Sava Ilića"}}
        assert OSMSource()._parse_elements([el]) == []


class TestOSMMetadata:
    def test_names_and_links(self):
        p = _parse_one({
            "name": "Tvrđava Mamula", "name:en": "Fort Mamula", "name:ru": "Форт Мамула",
            "alt_name": "Mamula;Fort", "wikidata": "Q260715", "wikipedia": "en:Mamula",
            "description": "Island fortress", "start_date": "1853", "historic:period": "austro-hungarian",
            "access": "no", "website": "https://example.org", "historic": "fort",
        })
        m = p.metadata
        assert p.name == "Tvrđava Mamula"
        assert m["name_en"] == "Fort Mamula" and m["name_ru"] == "Форт Мамула"
        assert m["alt_names"] == ["Mamula", "Fort"]
        assert m["wikidata_id"] == "Q260715"
        assert m["wikipedia_lang"] == "en" and m["wikipedia_title"] == "Mamula"
        assert m["wikipedia_url"] == "https://en.wikipedia.org/wiki/Mamula"
        assert m["start_date"] == "1853" and m["historic_period"] == "austro-hungarian"
        assert m["access"] == "no" and m["website"] == "https://example.org"
        assert p.description == "Island fortress"
        assert m["osm_url"] == "https://www.openstreetmap.org/node/1"
        assert m["osm_tags"]["historic"] == "fort"  # full tag dict kept for LLM prompts

    def test_name_en_is_not_repeated_when_it_is_the_name(self):
        p = _parse_one({"name:en": "Fort", "historic": "fort"})
        assert p.name == "Fort"
        assert "name_en" not in p.metadata

    def test_invalid_wikidata_and_wikipedia_are_ignored(self):
        p = _parse_one({"name": "x", "wikidata": "nope", "wikipedia": "no-language-prefix"})
        assert "wikidata_id" not in p.metadata
        assert "wikipedia_url" not in p.metadata

    def test_commons_file_becomes_photo(self):
        p = _parse_one({"name": "x", "historic": "fort",
                        "wikimedia_commons": "File:Fort Mamula.jpg"})
        assert p.photos == [
            "https://commons.wikimedia.org/wiki/Special:FilePath/Fort_Mamula.jpg?width=640"
        ]

    def test_commons_category_is_kept_as_metadata_not_photo(self):
        p = _parse_one({"name": "x", "wikimedia_commons": "Category:Fort Mamula"})
        assert p.photos == []
        assert p.metadata["commons_category"] == "Category:Fort Mamula"

    def test_image_url_becomes_https_photo(self):
        p = _parse_one({"name": "x", "image": "http://example.org/a.jpg"})
        assert p.photos == ["https://example.org/a.jpg"]

    def test_tag_list_has_no_addresses_or_names(self):
        p = _parse_one({"military": "bunker", "name": "B", "addr:street": "Park Street",
                        "source": "survey"})
        assert p.tags == ["military=bunker"]

    def test_restaurants_need_marker(self):
        assert _parse_one({"amenity": "restaurant"}).categories == []
        assert PlaceCategory.RESTAURANT_NOTABLE in _parse_one(
            {"amenity": "restaurant", "wikipedia": "en:X"}).categories

    def test_tag_derived_categories_are_marked_explicit(self):
        p = _parse_one({"military": "bunker"})
        assert p.category_confidence and all(v >= 0.8 for v in p.category_confidence.values())

    def test_relation_center_used(self):
        el = {"type": "relation", "id": 3, "center": {"lat": 1.5, "lon": 2.5},
              "tags": {"historic": "fort", "name": "F"}}
        p = OSMSource()._parse_elements([el])[0]
        assert p.id == "osm_relation_3" and p.coordinates.lat == 1.5

    def test_real_world_montenegro_tags(self):
        """Tag sets seen in the wild (Herceg Novi / Luštica)."""
        pillbox = _parse_one({"building": "bunker", "bunker_type": "pillbox",
                              "landuse": "military", "military": "bunker"})
        assert pillbox.name == "Bunker"
        assert {PlaceCategory.MILITARY, PlaceCategory.UNDERGROUND} <= set(pillbox.categories)
        station = _parse_one({"abandoned:railway": "station", "railway": "site", "name": "Igalo"})
        assert station.name == "Igalo"
        assert {PlaceCategory.ABANDONED, PlaceCategory.TRANSPORT} <= set(station.categories)
        mamula_ruin = _parse_one({"historic": "ruins", "ruins": "building", "name": "Zatvor"})
        assert PlaceCategory.RUINS in mamula_ruin.categories


# ── The Overpass query ───────────────────────────────────────────────────────

_BBOX = "42.0,18.0,42.1,18.1"


class TestOverpassQuery:
    def test_every_statement_is_bounded_and_output_has_centers(self):
        q = build_overpass_query(_BBOX, 25, max_elements=1234)
        assert q.startswith("[out:json][timeout:25];")
        assert q.rstrip().endswith("out center tags 1234;")
        statements = [ln.strip() for ln in q.splitlines() if ln.strip().startswith(("nwr", "node", "way"))]
        assert len(statements) == len(_GEM_CLAUSES) + len(_LANDMARK_CLAUSES)
        assert all(s.endswith(f"({_BBOX});") for s in statements)

    def test_no_key_regex_statements(self):
        # `[~"key-regex"~"..."]` scans every object in the bbox (>20 s measured on Overpass).
        assert all("[~" not in c for c in _GEM_CLAUSES + _LANDMARK_CLAUSES)

    def test_brackets_and_quotes_are_balanced(self):
        for clause in _GEM_CLAUSES + _LANDMARK_CLAUSES:
            assert clause.count("[") == clause.count("]"), clause
            assert clause.count('"') % 2 == 0, clause
            assert clause.count("(") == clause.count(")"), clause

    def test_query_size_is_sane(self):
        q = build_overpass_query(_BBOX, 25)
        assert len(q) < 20_000
        assert len(_GEM_CLAUSES) + len(_LANDMARK_CLAUSES) < 100

    @pytest.mark.parametrize("needle", [
        '"abandoned"', '"disused"', '"ruins"', '"building"~"^(ruins|collapsed)$"', '"ruined"="yes"',
        '"abandoned:building"', '"disused:building"', '"abandoned:railway"', '"disused:railway"',
        '"abandoned:amenity"', '"abandoned:military"', '"disused:man_made"',
        "charcoal_pile", "wreck", "battlefield", "bunker", "barracks", "airfield", "trench",
        "adit", "mineshaft", "water_tower", "lighthouse", "silo", "cave_entrance", "sinkhole",
        '"tunnel:name"', "railway", "industrial", "archaeological_site", "fort", "castle", "mine",
    ])
    def test_gem_clauses_cover(self, needle):
        assert needle in "\n".join(_GEM_CLAUSES)

    def test_gems_exclude_mundane_noise(self):
        text = "\n".join(_GEM_CLAUSES)
        assert '["shop"!~"."]' in text  # closed shops
        assert "culvert" in text  # drainage tunnels
        assert "communication" in text  # radio masts
        assert "residential" in text  # streets that used to be railways

    def test_landmarks_are_a_separate_tier(self):
        gems = "\n".join(_GEM_CLAUSES)
        for needle in ("museum", "memorial", "viewpoint", "attraction", "cathedral"):
            assert needle not in gems
            assert needle in "\n".join(_LANDMARK_CLAUSES)

    def test_default_clauses_include_both_tiers(self):
        q = build_overpass_query(_BBOX, 25)
        assert "cave_entrance" in q and "museum" in q


# ── search(): HTTP, tiers, mirrors, cache (no network: pytest-httpx) ────────

PRIMARY = "https://overpass-api.de/api/interpreter"
MIRROR = "https://mirror.test/api/interpreter"

_GEM_ELEMENT = {"type": "node", "id": 1, "lat": 42.45, "lon": 18.53,
                "tags": {"military": "bunker", "abandoned": "yes", "name": "Old Bunker"}}
_LANDMARK_ELEMENT = {"type": "node", "id": 2, "lat": 42.46, "lon": 18.54,
                     "tags": {"tourism": "museum", "name": "City Museum"}}


@pytest.fixture
async def osm(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sources.osm.disk_cache", DiskCache(cache_dir=str(tmp_path), ttl=3600))
    monkeypatch.setattr(settings, "overpass_url", PRIMARY)
    monkeypatch.setattr(settings, "overpass_fallback_urls", MIRROR)
    http_client._client = None
    source = OSMSource()
    source._rate_limiter = RateLimiter(max_requests=1000, window_seconds=1)
    yield source
    await close_http_client()


def _tier_of(request: httpx.Request) -> str:
    query = parse_qs(request.content.decode())["data"][0]
    return "gems" if "cave_entrance" in query else "landmarks"


class _Overpass:
    """Scriptable fake Overpass: per-URL, per-tier responses; records every request."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.handlers: dict[tuple[str, str], object] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = (str(request.url), _tier_of(request))
        self.calls.append(key)
        handler = self.handlers.get(key, self.handlers.get((key[0], "*")))
        if handler is None:
            return httpx.Response(500)
        return handler() if callable(handler) else handler


def _ok(*elements, **extra):
    return httpx.Response(200, json={"elements": list(elements), **extra})


@pytest.fixture
def overpass(httpx_mock):
    fake = _Overpass()
    httpx_mock.add_callback(fake, is_reusable=True, is_optional=True)
    return fake


class TestOSMSearch:
    async def test_queries_both_tiers_and_merges_results(self, osm, overpass):
        overpass.handlers = {(PRIMARY, "gems"): _ok(_GEM_ELEMENT),
                             (PRIMARY, "landmarks"): _ok(_LANDMARK_ELEMENT)}
        places = await osm.search(42.45, 18.53, 5.0)
        assert {p.name for p in places} == {"Old Bunker", "City Museum"}
        assert sorted(t for _, t in overpass.calls) == ["gems", "landmarks"]

    async def test_big_radius_skips_the_landmark_tier(self, osm, overpass):
        overpass.handlers = {(PRIMARY, "gems"): _ok(_GEM_ELEMENT)}
        places = await osm.search(42.45, 18.53, 30.0)
        assert [p.name for p in places] == ["Old Bunker"]
        assert [t for _, t in overpass.calls] == ["gems"]

    async def test_second_search_is_served_from_cache(self, osm, overpass):
        overpass.handlers = {(PRIMARY, "gems"): _ok(_GEM_ELEMENT),
                             (PRIMARY, "landmarks"): _ok(_LANDMARK_ELEMENT)}
        first = await osm.search(42.45, 18.53, 5.0)
        n = len(overpass.calls)
        second = await osm.search(42.45, 18.53, 5.0)
        assert len(overpass.calls) == n
        assert [p.id for p in second] == [p.id for p in first]

    async def test_falls_back_to_mirror_on_server_error(self, osm, overpass):
        overpass.handlers = {(PRIMARY, "*"): httpx.Response(504),
                             (MIRROR, "gems"): _ok(_GEM_ELEMENT),
                             (MIRROR, "landmarks"): _ok(_LANDMARK_ELEMENT)}
        places = await osm.search(42.45, 18.53, 5.0)
        assert len(places) == 2
        assert {u for u, _ in overpass.calls} == {PRIMARY, MIRROR}

    async def test_runtime_error_remark_is_a_failure_not_an_empty_result(self, osm, overpass):
        remark = 'runtime error: Query timed out in "query" at line 3 after 30 seconds.'
        overpass.handlers = {(PRIMARY, "*"): _ok(remark=remark),
                             (MIRROR, "*"): _ok(_GEM_ELEMENT)}
        places = await osm.search(42.45, 18.53, 30.0)
        assert [p.name for p in places] == ["Old Bunker"]

    async def test_all_endpoints_failing_raises(self, osm, overpass):
        overpass.handlers = {(PRIMARY, "*"): httpx.Response(429), (MIRROR, "*"): httpx.Response(504)}
        with pytest.raises(OverpassError):
            await osm.search(42.45, 18.53, 5.0)

    async def test_one_failing_tier_keeps_the_other(self, osm, overpass, monkeypatch):
        monkeypatch.setattr(settings, "overpass_fallback_urls", "")
        overpass.handlers = {(PRIMARY, "gems"): httpx.Response(500),
                             (PRIMARY, "landmarks"): _ok(_LANDMARK_ELEMENT)}
        places = await osm.search(42.45, 18.53, 5.0)
        assert [p.name for p in places] == ["City Museum"]

    async def test_partial_result_with_remark_is_returned_but_not_cached(self, osm, overpass):
        overpass.handlers = {(PRIMARY, "gems"): _ok(_GEM_ELEMENT, remark="runtime remark: partial"),
                             (PRIMARY, "landmarks"): _ok()}
        assert len(await osm.search(42.45, 18.53, 5.0)) == 1
        n = len(overpass.calls)
        await osm.search(42.45, 18.53, 5.0)
        assert len(overpass.calls) > n  # gems tier asked again; the clean landmark tier was cached

    async def test_duplicate_elements_across_tiers_are_deduplicated(self, osm, overpass):
        overpass.handlers = {(PRIMARY, "gems"): _ok(_GEM_ELEMENT),
                             (PRIMARY, "landmarks"): _ok(_GEM_ELEMENT)}
        assert len(await osm.search(42.45, 18.53, 5.0)) == 1

    async def test_request_carries_the_query_as_form_data(self, osm, overpass, httpx_mock):
        overpass.handlers = {(PRIMARY, "*"): _ok()}
        await osm.search(42.45, 18.53, 5.0)
        request = httpx_mock.get_requests()[0]
        query = parse_qs(request.content.decode())["data"][0]
        assert query.startswith("[out:json]") and "out center tags" in query
