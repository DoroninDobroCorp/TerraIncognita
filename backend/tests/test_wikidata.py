"""Tests for Story 1.3 — Wikidata source."""

from __future__ import annotations

import re

import httpx
import pytest

from app.config import settings
from app.models.place import PlaceCategory, PlaceSource
from app.services.cache import DiskCache
from app.sources import wikidata as wikidata_module
from app.sources.wikidata import WikidataSource
from app.utils import http_client

_SPARQL_RESPONSE = {
    "results": {
        "bindings": [
            {
                "item": {"value": "http://www.wikidata.org/entity/Q12345"},
                "itemLabel": {"value": "Ancient Fortress"},
                "itemDescription": {"value": "A medieval fortress"},
                "coord": {"value": "Point(18.53 42.45)"},
                "type": {"value": "http://www.wikidata.org/entity/Q57821"},
                "sitelink": {"value": "https://en.wikipedia.org/wiki/Ancient_Fortress"},
            },
            {
                "item": {"value": "http://www.wikidata.org/entity/Q67890"},
                "itemLabel": {"value": "Hidden Cave"},
                "itemDescription": {"value": "A natural cave system"},
                "coord": {"value": "Point(18.52 42.44)"},
                "type": {"value": "http://www.wikidata.org/entity/Q35509"},
            },
            {
                "item": {"value": "http://www.wikidata.org/entity/Q11111"},
                "itemLabel": {"value": "Q11111"},
                "coord": {"value": "Point(18.51 42.43)"},
                "type": {"value": "http://www.wikidata.org/entity/Q109607"},  # ruins
            },
        ]
    }
}


class TestWikidata:
    def setup_method(self):
        self.source = WikidataSource()

    def test_parse_results_count(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert len(places) == 3

    def test_place_id_format(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert places[0].id == "wiki_Q12345"

    def test_source_set(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert places[0].source == PlaceSource.WIKIDATA

    def test_coordinates_parsing(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        # Point(lng lat) → lat=42.45, lng=18.53
        assert places[0].coordinates.lat == pytest.approx(42.45)
        assert places[0].coordinates.lng == pytest.approx(18.53)

    def test_category_mapping_fortification(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert PlaceCategory.MILITARY in places[0].categories

    def test_category_mapping_cave(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert PlaceCategory.CAVE in places[1].categories
        assert PlaceCategory.UNDERGROUND in places[1].categories

    def test_category_mapping_ruin(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert PlaceCategory.RUINS in places[2].categories

    def test_confidence_with_wikipedia(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        # Has Wikipedia → less unusual → lower confidence
        assert places[0].confidence == 0.6

    def test_confidence_without_wikipedia(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        # No Wikipedia → more unusual → higher confidence
        assert places[1].confidence == 0.7

    def test_label_equal_to_qid_is_replaced_by_a_generated_name(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert places[2].name and places[2].name != places[2].metadata["wikidata_id"]
        assert places[2].metadata["name_generated"] is True

    def test_metadata_contains_wikidata_id(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert places[0].metadata["wikidata_id"] == "Q12345"
        assert places[0].metadata["wikipedia_url"].startswith("https://")

    def test_dedup_same_qid(self):
        data = {
            "results": {
                "bindings": [
                    {
                        "item": {"value": "http://www.wikidata.org/entity/Q999"},
                        "itemLabel": {"value": "Place"},
                        "coord": {"value": "Point(18.5 42.4)"},
                        "type": {"value": "http://www.wikidata.org/entity/Q82117"},
                    },
                    {
                        "item": {"value": "http://www.wikidata.org/entity/Q999"},
                        "itemLabel": {"value": "Place"},
                        "coord": {"value": "Point(18.5 42.4)"},
                        "type": {"value": "http://www.wikidata.org/entity/Q57821"},
                    },
                ]
            }
        }
        places = self.source._parse_results(data)
        assert len(places) == 1

    def test_parse_point_invalid(self):
        lat, lng = WikidataSource._parse_point("invalid")
        assert lat is None
        assert lng is None

    def test_parse_point_valid(self):
        lat, lng = WikidataSource._parse_point("Point(18.53 42.45)")
        assert lat == pytest.approx(42.45)
        assert lng == pytest.approx(18.53)


# ── Verified type ids, sitelinks, failure semantics ──────────────────────────


def _binding(qid: str, type_qid: str, label: str = "Thing", **extra) -> dict:
    return {
        "item": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "itemLabel": {"value": label},
        "coord": {"value": "Point(18.5 42.4)"},
        "type": {"value": f"http://www.wikidata.org/entity/{type_qid}"},
        **extra,
    }


@pytest.mark.parametrize(("type_qid", "label", "expected"), [
    ("Q109607", "ruins", {PlaceCategory.RUINS}),
    ("Q91122", "bunker", {PlaceCategory.MILITARY, PlaceCategory.UNDERGROUND}),
    ("Q389959", "air-raid shelter", {PlaceCategory.MILITARY, PlaceCategory.UNDERGROUND}),
    ("Q106765618", "abandoned building", {PlaceCategory.ABANDONED}),
    ("Q4663385", "former railway station", {PlaceCategory.ABANDONED, PlaceCategory.TRANSPORT}),
    ("Q74047", "ghost town", {PlaceCategory.ABANDONED}),
    ("Q820477", "mine", {PlaceCategory.INDUSTRIAL, PlaceCategory.UNDERGROUND}),
    ("Q44377", "tunnel", {PlaceCategory.UNDERGROUND}),
    ("Q1785071", "fort", {PlaceCategory.MILITARY, PlaceCategory.LANDMARK}),
    ("Q852190", "shipwreck", {PlaceCategory.UNDERWATER}),
    ("Q274153", "water tower", {PlaceCategory.INDUSTRIAL}),
    ("Q34038", "waterfall", {PlaceCategory.WATER, PlaceCategory.NATURE_HIDDEN}),
    ("Q39715", "lighthouse", {PlaceCategory.LANDMARK}),
    ("Q82117", "city gate", {PlaceCategory.ARCHITECTURE}),
    ("Q12518", "tower", {PlaceCategory.ARCHITECTURE}),
])
def test_type_ids_map_to_what_they_are(type_qid, label, expected):
    """Each Q-id was checked against Wikidata's own label (the old table had wrong ones)."""
    place = WikidataSource()._parse_results({"results": {"bindings": [_binding("Q1", type_qid)]}})[0]
    assert expected <= set(place.categories), label


def test_wrongly_mapped_type_ids_are_gone():
    # Q863813 is a university, Q191992 a headland, Q39614 a cemetery, Q12518 is not underground.
    for bad in ("Q863813", "Q191992", "Q39614"):
        assert bad not in wikidata_module._TYPE_MAP
    assert PlaceCategory.UNDERGROUND not in wikidata_module._TYPE_MAP["Q12518"]


def test_sitelink_count_is_exposed_for_scoring():
    data = {"results": {"bindings": [
        _binding("Q1", "Q23413", sitelinks={"value": "57"}),
        _binding("Q2", "Q23413", sitelinks={"value": "0"}),
        _binding("Q3", "Q23413"),
        _binding("Q4", "Q23413", sitelinks={"value": "many"}),
    ]}}
    p1, p2, p3, p4 = WikidataSource()._parse_results(data)
    assert p1.metadata["sitelinks"] == 57 and p2.metadata["sitelinks"] == 0
    assert "sitelinks" not in p3.metadata and "sitelinks" not in p4.metadata


def test_sparql_asks_for_sitelinks_and_every_known_type():
    query = wikidata_module._SPARQL_TEMPLATE.format(
        south=1, west=2, north=3, east=4,
        type_values=" ".join(f"wd:{q}" for q in wikidata_module._TYPE_MAP),
        common_values=", ".join(f"wd:{q}" for q in wikidata_module._COMMON_TYPES),
        limit=wikidata_module._QUERY_LIMIT)
    assert "wikibase:sitelinks ?sitelinks" in query
    assert all(f"wd:{q}" in query for q in wikidata_module._TYPE_MAP)
    assert 'Point(2 1)' in query and 'Point(4 3)' in query
    # when the row limit bites, the common types (churches, museums...) go first, not the rare ones
    assert query.index("ORDER BY ?common") < query.index(f"LIMIT {wikidata_module._QUERY_LIMIT}")
    assert set(wikidata_module._COMMON_TYPES) <= set(wikidata_module._TYPE_MAP)


@pytest.fixture
async def wd(tmp_path, monkeypatch):
    monkeypatch.setattr(wikidata_module, "disk_cache", DiskCache(cache_dir=str(tmp_path), ttl=3600))
    http_client._client = None
    yield WikidataSource()
    await http_client.close_http_client()


class TestWikidataSearch:
    async def test_success_is_cached_and_polite(self, wd, httpx_mock):
        httpx_mock.add_response(url=re.compile(r"https://query\.wikidata\.org/sparql.*"),
                                json=_SPARQL_RESPONSE)
        first = await wd.search(42.45, 18.53, 5.0)
        second = await wd.search(42.45, 18.53, 5.0)
        assert len(first) == 3 and [p.id for p in second] == [p.id for p in first]
        [request] = httpx_mock.get_requests()
        assert request.headers["user-agent"] == settings.wikimedia_user_agent
        assert request.headers["accept"] == "application/sparql-results+json"

    async def test_outage_raises_and_is_not_cached_as_empty(self, wd, httpx_mock):
        httpx_mock.add_response(status_code=504)
        with pytest.raises(httpx.HTTPStatusError):
            await wd.search(42.45, 18.53, 5.0)
        httpx_mock.add_response(json=_SPARQL_RESPONSE)
        assert len(await wd.search(42.45, 18.53, 5.0)) == 3  # recovered: nothing stale was cached

    async def test_timeout_raises(self, wd, httpx_mock):
        httpx_mock.add_exception(httpx.ReadTimeout("slow"))
        with pytest.raises(httpx.ReadTimeout):
            await wd.search(42.45, 18.53, 5.0)


class TestUnlabelledItems:
    def _binding(self, label: str, description: str | None):
        b = {
            "item": {"value": "http://www.wikidata.org/entity/Q27569554"},
            "itemLabel": {"value": label},
            "coord": {"value": "Point(18.5 42.4)"},
            "type": {"value": "http://www.wikidata.org/entity/Q35509"},  # cave
        }
        if description:
            b["itemDescription"] = {"value": description}
        return b

    def test_bare_qid_label_becomes_an_honest_generated_name(self):
        src = WikidataSource()
        place = src._parse_results({"results": {"bindings": [self._binding("Q27569554", "cave in Herceg Novi")]}})[0]
        assert place.name == "Cave in Herceg Novi"
        assert place.metadata["name_generated"] is True

    def test_falls_back_to_the_category_without_a_description(self):
        src = WikidataSource()
        place = src._parse_results({"results": {"bindings": [self._binding("Q27569554", None)]}})[0]
        assert place.name and place.name != "Q27569554"
        assert place.metadata["name_generated"] is True

    def test_real_labels_are_untouched(self):
        src = WikidataSource()
        place = src._parse_results({"results": {"bindings": [self._binding("Golubinja Pećina", "cave")]}})[0]
        assert place.name == "Golubinja Pećina"
        assert "name_generated" not in place.metadata


def _row(qid, label, type_qid, lat=42.45, lng=18.53):
    return {
        "item": {"value": f"http://www.wikidata.org/entity/{qid}"}, "itemLabel": {"value": label},
        "coord": {"value": f"Point({lng} {lat})"}, "type": {"value": f"http://www.wikidata.org/entity/{type_qid}"},
    }


class TestTypeRowsAndTruncation:
    def test_every_type_of_an_item_counts_whatever_order_the_rows_come_in(self):
        src = WikidataSource()
        rows = [_row("Q1", "Kula", "Q109607"), _row("Q1", "Kula", "Q35509")]  # ruins, cave
        forward = src._parse_results({"results": {"bindings": rows}})
        backward = src._parse_results({"results": {"bindings": rows[::-1]}})
        assert len(forward) == len(backward) == 1
        wanted = {*wikidata_module._TYPE_MAP["Q109607"], *wikidata_module._TYPE_MAP["Q35509"]}
        assert set(forward[0].categories) == set(backward[0].categories) == wanted
        assert PlaceCategory.RUINS in wanted and len(wanted) > 1

    def test_merging_type_rows_does_not_write_into_the_shared_type_map(self):
        src = WikidataSource()
        before = {k: list(v) for k, v in wikidata_module._TYPE_MAP.items()}
        src._parse_results({"results": {"bindings": [
            _row("Q1", "A", "Q109607"), _row("Q1", "A", "Q35509"), _row("Q2", "B", "Q109607"),
        ]}})
        assert {k: list(v) for k, v in wikidata_module._TYPE_MAP.items()} == before

    async def test_a_row_limited_answer_is_partial_and_the_gap_is_remembered(self, wd, httpx_mock, monkeypatch):
        from app.sources.base import PartialSourceResult

        monkeypatch.setattr(wikidata_module, "_QUERY_LIMIT", 3)
        httpx_mock.add_response(url=re.compile(r"https://query\.wikidata\.org/sparql.*"), json=_SPARQL_RESPONSE)
        for _ in range(2):  # the second call is served from the cache and is still flagged
            with pytest.raises(PartialSourceResult) as caught:
                await wd.search(42.45, 18.53, 5.0)
            assert len(caught.value.places) == 3
        assert len(httpx_mock.get_requests()) == 1

    async def test_an_answer_below_the_limit_is_complete(self, wd, httpx_mock, monkeypatch):
        monkeypatch.setattr(wikidata_module, "_QUERY_LIMIT", 4)
        httpx_mock.add_response(url=re.compile(r"https://query\.wikidata\.org/sparql.*"), json=_SPARQL_RESPONSE)
        assert len(await wd.search(42.45, 18.53, 5.0)) == 3

    async def test_a_bare_list_in_the_cache_is_not_trusted(self, wd, httpx_mock):
        """Entries written before completeness was tracked were plain lists: re-ask rather than serve them."""
        south, west, north, east = wikidata_module.bounding_box(42.45, 18.53, 5.0)
        await wikidata_module.disk_cache.set(f"wiki2_{south:.3f}_{west:.3f}_{north:.3f}_{east:.3f}", [])
        httpx_mock.add_response(url=re.compile(r"https://query\.wikidata\.org/sparql.*"), json=_SPARQL_RESPONSE)
        assert len(await wd.search(42.45, 18.53, 5.0)) == 3


def test_the_timeout_leaves_room_for_a_dense_city_centre():
    """Measured against the live service: Vienna (10 km) 7-17 s, Paris (20 km) 29 s; it cuts queries off at 60 s."""
    assert 45 <= settings.wikidata_timeout <= 60


class TestRowOrderDoesNotMatter:
    def test_categories_order_and_type_are_the_same_for_every_row_order(self):
        import itertools

        types = ["Q109607", "Q35509", "Q839954"]  # ruins, cave, archaeological site
        outcomes = set()
        for order in itertools.permutations(types):
            rows = [_row("Q1", "Kula", t) for t in order]
            [place] = WikidataSource()._parse_results({"results": {"bindings": rows}})
            outcomes.add((tuple(c.value for c in place.categories), place.metadata["type_qid"], place.name))
        assert len(outcomes) == 1, outcomes

    def test_the_smallest_type_id_is_reported(self):
        rows = [_row("Q1", "Kula", "Q35509"), _row("Q1", "Kula", "Q109607")]
        [place] = WikidataSource()._parse_results({"results": {"bindings": rows}})
        assert place.metadata["type_qid"] == min("Q35509", "Q109607")

    def test_an_unlabelled_item_gets_the_same_generated_name_whatever_the_order(self):
        rows = [_row("Q9", "Q9", "Q109607"), _row("Q9", "Q9", "Q35509")]
        names = {WikidataSource()._parse_results({"results": {"bindings": r}})[0].name for r in (rows, rows[::-1])}
        assert len(names) == 1
