"""Collections: visit-to-place matching, per-category counts, and the endpoint."""

from __future__ import annotations

import random
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.map_config import _CATEGORY_STYLES
from app.main import app
from app.middleware import RATE_RULES
from app.models.journal import Visit, VisitStatus
from app.models.place import Coordinates, DiscoverResponse, Place, PlaceCategory, PlaceSource
from app.services.place_collections import (
    find_visited_place_ids,
    names_similar,
    summarize_collections,
)

client = TestClient(app)

C = PlaceCategory


def mk_place(pid: str, name: str | None, lat: float, lng: float, *cats: PlaceCategory) -> Place:
    return Place(
        id=pid, source=PlaceSource.OSM, name=name, categories=list(cats),
        coordinates=Coordinates(lat=lat, lng=lng),
    )


def mk_visit(pid: str, lat: float, lng: float, name: str | None = None, vid: str = "") -> Visit:
    return Visit(
        id=vid or f"visit_{pid}_{lat}_{lng}", place_id=pid, place_name=name,
        status=VisitStatus.VISITED, coordinates=Coordinates(lat=lat, lng=lng),
    )


def _discover_returning(places: list[Place], has_more: bool = False):
    return patch(
        "app.services.place_collections.discover",
        new_callable=AsyncMock,
        return_value=DiscoverResponse(
            places=places, total=len(places), has_more=has_more, cursor=None,
        ),
    )


class TestNamesSimilar:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Fort Mamula", "fort mamula"),
            ("Fort Mamula", "Mamula"),
            ("Mamula Fort", "Fort Mamula Island"),
            ("Старая крепость", "старая  Крепость!"),
            ("St. Nicholas", "St Nicholas"),
            (None, "Anything"),
            ("Anything", ""),
            ("   ", "x"),
            ("!!!", "Fort Mamula"),
        ],
    )
    def test_similar(self, a, b):
        assert names_similar(a, b)

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Fort Mamula", "Water Tower"),
            ("Old Bunker", "Old Bridge"),
            ("art", "party"),
            ("Comfort", "Fort"),
            ("Пещера", "Крепость"),
        ],
    )
    def test_different(self, a, b):
        assert not names_similar(a, b)


class TestFindVisitedPlaceIds:
    def test_match_by_id(self):
        places = [mk_place("a", "A", 42.0, 18.0), mk_place("b", "B", 42.1, 18.1)]
        # coordinates deliberately far away: the id alone decides
        visits = [mk_visit("a", 10.0, 10.0, "something else")]
        assert find_visited_place_ids(places, visits, 100) == {"a"}

    def test_match_by_proximity_and_name(self):
        places = [mk_place("a", "Fort Mamula", 42.4000, 18.5000)]
        visits = [mk_visit("legacy-id", 42.4004, 18.5000, "fort mamula")]  # ~45 m
        assert find_visited_place_ids(places, visits, 100) == {"a"}

    def test_same_spot_but_different_name_is_not_a_match(self):
        places = [mk_place("a", "Water Tower", 42.4000, 18.5000)]
        visits = [mk_visit("x", 42.4000, 18.5000, "Fort Mamula")]
        assert find_visited_place_ids(places, visits, 100) == set()

    def test_outside_the_proximity_radius_is_not_a_match(self):
        places = [mk_place("a", "Fort", 42.4000, 18.5000)]
        visits = [mk_visit("x", 42.4020, 18.5000, "Fort")]  # ~220 m
        assert find_visited_place_ids(places, visits, 100) == set()
        assert find_visited_place_ids(places, visits, 300) == {"a"}

    def test_nameless_visit_claims_only_the_nearest_neighbour(self):
        places = [
            mk_place("near", "Alpha", 42.40000, 18.5000),
            mk_place("far", "Beta", 42.40040, 18.5000),
        ]
        visits = [mk_visit("x", 42.40005, 18.5000, None)]
        assert find_visited_place_ids(places, visits, 100) == {"near"}

    def test_two_visits_can_claim_two_neighbours_but_not_the_same_one_twice(self):
        places = [
            mk_place("p1", "Alpha", 42.40000, 18.5000),
            mk_place("p2", "Beta", 42.40030, 18.5000),
        ]
        v1 = mk_visit("x1", 42.40001, 18.5000, None, vid="v1")
        v2 = mk_visit("x2", 42.40002, 18.5000, None, vid="v2")
        assert find_visited_place_ids(places, [v1, v2], 100) == {"p1", "p2"}
        assert find_visited_place_ids(places, [v1], 100) == {"p1"}

    def test_unnamed_place_matches_by_proximity(self):
        places = [mk_place("a", None, 42.4000, 18.5000)]
        visits = [mk_visit("x", 42.4001, 18.5000, "Whatever it was called")]
        assert find_visited_place_ids(places, visits, 100) == {"a"}

    def test_id_match_does_not_swallow_neighbours(self):
        places = [mk_place("a", "Alpha", 42.4000, 18.5000), mk_place("b", "Beta", 42.4001, 18.5000)]
        visits = [mk_visit("a", 42.4000, 18.5000, "Alpha")]
        assert find_visited_place_ids(places, visits, 100) == {"a"}

    def test_no_visits_no_places(self):
        assert find_visited_place_ids([], [], 100) == set()
        assert find_visited_place_ids([mk_place("a", "A", 1, 1)], [], 100) == set()

    def test_thousands_of_visits_stay_fast(self):
        rng = random.Random(7)
        places = [mk_place(f"p{i}", f"Place {i}", 42 + rng.random(), 18 + rng.random()) for i in range(200)]
        visits = [mk_visit(f"v{i}", 42 + rng.random(), 18 + rng.random(), f"Visit {i}") for i in range(5000)]
        t0 = time.perf_counter()
        find_visited_place_ids(places, visits, 100)
        assert time.perf_counter() - t0 < 1.0


class TestSummarize:
    def test_counts_per_category_sorted_by_total(self):
        places = [
            mk_place("1", "a", 1, 1, C.ABANDONED),
            mk_place("2", "b", 1, 1, C.ABANDONED),
            mk_place("3", "c", 1, 1, C.ABANDONED),
            mk_place("4", "d", 1, 1, C.CAVE),
            mk_place("5", "e", 1, 1, C.WATER),
            mk_place("6", "f", 1, 1, C.WATER),
        ]
        out = summarize_collections(places, {"1", "3", "5"})
        assert [(c.category, c.visited, c.total) for c in out] == [
            ("abandoned", 2, 3), ("water", 1, 2), ("cave", 0, 1),
        ]

    def test_multi_category_place_counts_in_each_category(self):
        places = [mk_place("1", "bunker", 1, 1, C.MILITARY, C.UNDERGROUND)]
        out = {c.category: (c.visited, c.total) for c in summarize_collections(places, {"1"})}
        assert out == {"military": (1, 1), "underground": (1, 1)}

    def test_duplicate_category_on_one_place_counts_once(self):
        places = [mk_place("1", "x", 1, 1, C.RUINS, C.RUINS)]
        assert summarize_collections(places, set())[0].total == 1

    def test_label_and_icon_come_from_map_config(self):
        out = summarize_collections([mk_place("1", "x", 1, 1, C.ABANDONED)], set())[0]
        assert out.label == _CATEGORY_STYLES["abandoned"].label
        assert out.icon == _CATEGORY_STYLES["abandoned"].icon

    def test_category_without_a_style_gets_a_generic_label_and_icon(self):
        out = summarize_collections([mk_place("1", "x", 1, 1, C.CULINARY)], set())[0]
        assert out.category == "culinary"
        assert out.label == "Culinary" and out.icon

    def test_empty(self):
        assert summarize_collections([], set()) == []

    def test_places_without_categories_are_ignored(self):
        assert summarize_collections([mk_place("1", "x", 1, 1)], set()) == []

    def test_ties_are_ordered_by_category_name(self):
        places = [mk_place("1", "a", 1, 1, C.WATER), mk_place("2", "b", 1, 1, C.CAVE)]
        assert [c.category for c in summarize_collections(places, set())] == ["cave", "water"]


class TestCollectionsEndpoint:
    def test_contract_shape_and_single_discover_call(self, fresh_journal):
        places = [
            mk_place("a", "Old Mill", 42.40, 18.50, C.INDUSTRIAL),
            mk_place("b", "Hidden Bunker", 42.41, 18.51, C.MILITARY, C.UNDERGROUND),
        ]
        with _discover_returning(places) as mock:
            resp = client.get("/api/collections?lat=42.4&lng=18.5&radius_km=30")
        assert resp.status_code == 200
        assert mock.await_count == 1
        req = mock.await_args.args[0]
        assert (req.lat, req.lng, req.radius_km, req.limit) == (42.4, 18.5, 30.0, 200)
        body = resp.json()
        assert set(body) == {"collections"}
        for item in body["collections"]:
            assert set(item) == {"category", "label", "icon", "visited", "total"}
        assert {c["category"] for c in body["collections"]} == {"industrial", "military", "underground"}

    def test_default_radius_is_50(self, fresh_journal):
        with _discover_returning([]) as mock:
            client.get("/api/collections?lat=1&lng=2")
        assert mock.await_args.args[0].radius_km == 50.0

    def test_empty_area(self, fresh_journal):
        with _discover_returning([]):
            resp = client.get("/api/collections?lat=1&lng=2")
        assert resp.json() == {"collections": []}

    def test_visits_in_the_real_journal_are_counted(self, fresh_journal):
        places = [
            mk_place("osm_1", "Old Mill", 42.40, 18.50, C.INDUSTRIAL),
            mk_place("osm_2", "Foundry", 42.42, 18.52, C.INDUSTRIAL),
            mk_place("osm_3", "Cave", 42.43, 18.53, C.CAVE),
        ]
        client.post("/api/visits", json={"place_id": "osm_1", "lat": 42.40, "lng": 18.50, "place_name": "Old Mill"})
        # a visit recorded under another id, but at the same spot with the same name
        client.post("/api/visits", json={"place_id": "legacy", "lat": 42.4301, "lng": 18.53, "place_name": "The Cave"})
        # planned / skipped places are not visits
        client.post("/api/visits", json={"place_id": "osm_2", "lat": 42.42, "lng": 18.52, "status": "want_to_visit"})
        with _discover_returning(places):
            body = client.get("/api/collections?lat=42.4&lng=18.5").json()
        by_cat = {c["category"]: (c["visited"], c["total"]) for c in body["collections"]}
        assert by_cat == {"industrial": (1, 2), "cave": (1, 1)}
        assert [c["category"] for c in body["collections"]] == ["industrial", "cave"]

    @pytest.mark.parametrize(
        "qs",
        ["lat=91&lng=0", "lat=0&lng=181", "lat=0&lng=0&radius_km=0", "lat=0&lng=0&radius_km=51", "lng=0", "lat=x&lng=0"],
    )
    def test_invalid_params_are_422(self, qs):
        assert client.get(f"/api/collections?{qs}").status_code == 422

    def test_discovery_failure_is_500_without_details(self, fresh_journal):
        with patch(
            "app.services.place_collections.discover",
            new_callable=AsyncMock, side_effect=RuntimeError("overpass token abc123"),
        ):
            resp = client.get("/api/collections?lat=1&lng=2")
        assert resp.status_code == 500
        assert "abc123" not in resp.text

    def test_truncated_page_still_answers(self, fresh_journal):
        with _discover_returning([mk_place("a", "A", 1, 1, C.RUINS)], has_more=True):
            assert client.get("/api/collections?lat=1&lng=2").status_code == 200


def test_collections_are_rate_limited_under_the_daily_rule():
    rule = next(r for r in RATE_RULES if r.matches("GET", "/api/collections"))
    assert rule.name == "daily"
    assert rule is next(r for r in RATE_RULES if r.matches("GET", "/api/daily"))
    assert not rule.matches("POST", "/api/daily")


class TestVisitsRecordedUnderMergedIds:
    def test_a_visit_under_a_folded_in_id_counts_for_the_merged_place(self):
        merged = mk_place("osm_node_1", "Fort Mamula", 42.45, 18.53, C.MILITARY)
        merged.metadata["merged_ids"] = ["wiki_Q123"]
        visits = [mk_visit("wiki_Q123", 42.45, 18.53, "Tvrđava Mamula")]
        assert find_visited_place_ids([merged], visits, 100.0) == {"osm_node_1"}

    def test_the_visit_id_may_also_be_the_primary_one(self):
        merged = mk_place("osm_node_1", "Fort Mamula", 42.45, 18.53, C.MILITARY)
        merged.metadata["merged_ids"] = ["wiki_Q123"]
        assert find_visited_place_ids([merged], [mk_visit("osm_node_1", 42.45, 18.53)], 100.0) == {"osm_node_1"}
