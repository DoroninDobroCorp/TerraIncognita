"""Golden-set regression: gems must rank above mainstream clutter, area by area.

Each ``tests/fixtures/overpass_*.json`` is an Overpass-style element list for one area plus a
hand-labelled ``expect`` block (see ``tests/golden.py``). They are run through the real
discovery pipeline (parse → fuse → classify → score → sort_by="unusual"); assertions are about
*ranks*, never exact scores, so the scoring weights can be re-tuned freely as long as the
human judgement encoded in the fixtures still holds.

To add a city: ``python -m tests.golden record <name> <lat> <lng> <radius_km>`` then label it.
"""

from __future__ import annotations

import math

import pytest

from app.models.place import Place
from app.utils.geo import haversine_distance_m
from tests.golden import (
    fixture_paths,
    load_fixture,
    parse_sources,
    ranking_table,
    ranks,
    run_pipeline,
)

PATHS = fixture_paths()
IDS = [p.stem.removeprefix("overpass_") for p in PATHS]


def labelled(fx: dict) -> dict[str, str]:
    expect = fx["expect"]
    return {**dict.fromkeys(expect["gems"], "G"), **dict.fromkeys(expect["clutter"], "x")}


@pytest.fixture(params=PATHS, ids=IDS)
def fx(request) -> dict:
    return load_fixture(request.param)


@pytest.fixture
async def result(fx) -> list[Place]:
    return await run_pipeline(fx, sort_by="unusual")


def test_there_are_at_least_three_areas():
    assert len(PATHS) >= 3


class TestFixtureIsWellFormed:
    def test_required_fields(self, fx):
        assert {"name", "description", "provenance", "center", "radius_km", "elements", "expect"} <= set(fx)
        assert fx["elements"] and len(fx["center"]) == 2
        expect = fx["expect"]
        assert expect["gems"] and expect["clutter"] and expect["top_n"] >= 3

    def test_labels_do_not_overlap_and_are_unique(self, fx):
        gems, clutter = fx["expect"]["gems"], fx["expect"]["clutter"]
        assert len(set(gems)) == len(gems) and len(set(clutter)) == len(clutter)
        assert not set(gems) & set(clutter)

    def test_labelled_ids_exist(self, fx):
        known = {p.id for batch in parse_sources(fx).values() for p in batch}
        for pid in [*fx["expect"]["gems"], *fx["expect"]["clutter"]]:
            assert pid in known, f"{pid} is not produced by the parsers (typo?)"

    def test_fixture_mixes_gems_with_plenty_of_clutter(self, fx):
        parsed = parse_sources(fx)["osm"]
        assert len(parsed) >= 30
        assert len(fx["expect"]["clutter"]) >= 8


class TestGoldenRanking:
    async def test_every_gem_is_in_the_top_n(self, fx, result):
        top_n = fx["expect"]["top_n"]
        rank = ranks(result, fx["expect"]["gems"])
        missing = {pid: r for pid, r in rank.items() if r >= top_n}
        assert not missing, (
            f"gems outside the top {top_n}: {missing}\n{ranking_table(result, top_n + 8, labelled(fx))}"
        )

    async def test_no_clutter_in_the_top_n(self, fx, result):
        top_n = fx["expect"]["top_n"]
        rank = ranks(result, fx["expect"]["clutter"])
        leaked = {pid: r for pid, r in rank.items() if r < top_n}
        assert not leaked, f"clutter inside the top {top_n}: {leaked}\n{ranking_table(result, top_n, labelled(fx))}"

    async def test_every_gem_outranks_every_clutter_item(self, fx, result):
        worst_gem = max(ranks(result, fx["expect"]["gems"]).values())
        best_clutter = min(ranks(result, fx["expect"]["clutter"]).values())
        assert worst_gem < best_clutter, ranking_table(result, best_clutter + 3, labelled(fx))

    async def test_scores_separate_the_two_groups(self, fx, result):
        by_id = {pid: next(p for p in result if pid in {p.id, *p.metadata.get("merged_ids", [])})
                 for pid in [*fx["expect"]["gems"], *fx["expect"]["clutter"]]}
        gem_scores = [by_id[i].unusual_score for i in fx["expect"]["gems"]]
        clutter_scores = [by_id[i].unusual_score for i in fx["expect"]["clutter"]]
        assert min(gem_scores) > max(clutter_scores)
        assert min(gem_scores) >= 0.5 and max(clutter_scores) <= 0.5

    async def test_gems_say_why(self, fx, result):
        by_id = {pid: next(p for p in result if pid in {p.id, *p.metadata.get("merged_ids", [])})
                 for pid in fx["expect"]["gems"]}
        unexplained = [i for i, p in by_id.items() if not p.unusual_reasons]
        assert not unexplained, unexplained

    async def test_first_three_are_distinct_sites(self, fx, result):
        if not fx["expect"].get("top3_distinct_sites"):
            pytest.skip("fixture does not require it")
        top = result[:3]
        for i, a in enumerate(top):
            for b in top[i + 1:]:
                d = haversine_distance_m(a.coordinates.lat, a.coordinates.lng,
                                         b.coordinates.lat, b.coordinates.lng)
                assert d > 100, f"{a.name!r} and {b.name!r} are {d:.0f} m apart\n{ranking_table(result, 8)}"

    async def test_unnamed_places_are_returned_but_never_lead(self, fx, result):
        generated = [p for p in result if p.metadata.get("name_generated")]
        assert generated, "fixture should contain unnamed-but-interesting objects"
        assert all(p.name for p in generated)  # honest generated names, not blanks
        assert not any(p.metadata.get("name_generated") for p in result[:3])

    async def test_nothing_is_lost_in_fusion(self, fx, result):
        parsed = {p.id for batch in parse_sources(fx).values() for p in batch}
        kept = {i for p in result for i in (p.id, *p.metadata.get("merged_ids", []))}
        assert kept == parsed

    async def test_expected_cross_source_merges(self, fx, result):
        for group in fx["expect"].get("also_merged", []):
            places = {id(p) for i in group for p in result
                      if i in {p.id, *p.metadata.get("merged_ids", [])}}
            assert len(places) == 1, f"{group} should be one place\n{ranking_table(result, 12)}"

    async def test_famous_wikidata_twin_is_not_a_gem(self, fx, result):
        """A Wikidata item with dozens of language editions merged into a place pulls it down."""
        famous = [p for p in result if (p.metadata.get("sitelinks") or 0) >= 30]
        assert famous, "fixture should contain a mainstream Wikidata twin"
        assert all(p.unusual_score < 0.35 for p in famous)

    async def test_ordering_is_deterministic(self, fx, result):
        again = await run_pipeline(fx, sort_by="unusual")
        assert [p.id for p in again] == [p.id for p in result]
        assert [p.unusual_score for p in again] == [p.unusual_score for p in result]

    async def test_scores_are_sane(self, fx, result):
        assert all(0.0 <= p.unusual_score <= 1.0 and math.isfinite(p.unusual_score) for p in result)
        scores = [p.unusual_score for p in result]
        assert max(scores) > 0.75 and min(scores) < 0.2  # the scale is actually used


class TestAgainstTheOldDefaultOrder:
    async def test_unusual_order_finds_gems_at_least_as_early_as_confidence_order(self, fx, result):
        confidence = await run_pipeline(fx, sort_by="confidence")
        top_n = fx["expect"]["top_n"]
        gems = fx["expect"]["gems"]
        found_unusual = sum(r < top_n for r in ranks(result, gems).values())
        found_confidence = sum(r < top_n for r in ranks(confidence, gems).values())
        assert found_unusual == len(gems)
        assert found_unusual >= found_confidence

    async def test_default_confidence_order_still_works(self, fx):
        places = await run_pipeline(fx, sort_by="confidence")
        confs = [p.confidence for p in places]
        assert confs == sorted(confs, reverse=True)
