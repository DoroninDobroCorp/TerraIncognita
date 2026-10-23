"""Tests for the unusualness score (rule-based, deterministic) and site diversification."""

from __future__ import annotations

import pytest

from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.scoring import (
    CATEGORY_WEIGHTS,
    apply_unusual_scores,
    diversify_by_site,
    score_place,
    unusual_sort_key,
)
from app.sources.osm import OSMSource

C = PlaceCategory
S = PlaceSource


def osm_place(tags: dict, **kw) -> Place:
    """A place the way the OSM source would build it from these tags."""
    el = {"type": "node", "id": kw.pop("id", 1), "lat": kw.pop("lat", 42.4),
          "lon": kw.pop("lng", 18.5), "tags": tags}
    place = OSMSource()._parse_elements([el])[0]
    for key, value in kw.items():
        setattr(place, key, value)
    return place


def plain(categories, *, name="Somewhere", source=S.WIKIDATA, sources=None, description=None,
          photos=None, metadata=None, tags=None, id="p", lat=42.4, lng=18.5) -> Place:
    return Place(
        id=id, source=source, sources=sources or [source], name=name, description=description,
        categories=list(categories), coordinates=Coordinates(lat=lat, lng=lng),
        photos=photos or [], metadata=metadata or {}, tags=tags or [],
    )


# ── Table: typical places land in the right band, for the right reasons ────────

# (id, place factory, min score, max score, reasons that must be present)
_BANDS = [
    ("documented abandoned bunker",
     lambda: osm_place({"military": "bunker", "abandoned": "yes", "name": "Fort Zlatica",
                        "description": "Yugoslav-era coastal bunker", "start_date": "1952"}),
     0.78, 1.0, {"military bunker", "abandoned"}),
    ("unnamed pillbox",
     lambda: osm_place({"military": "bunker", "bunker_type": "pillbox"}),
     0.55, 0.75, {"military bunker"}),
    ("abandoned railway station",
     lambda: osm_place({"abandoned:railway": "station", "railway": "site", "name": "Igalo"}),
     0.65, 0.85, {"abandoned"}),
    ("cave entrance",
     lambda: osm_place({"natural": "cave_entrance"}),
     0.55, 0.75, {"cave entrance"}),
    ("mine adit",
     lambda: osm_place({"man_made": "adit", "name": "Stari rudnik"}),
     0.65, 0.9, {"old mine workings"}),
    ("named ruins",
     lambda: osm_place({"historic": "ruins", "name": "Kasarna"}),
     0.65, 0.85, {"ruins"}),
    ("unnamed abandoned building",
     lambda: osm_place({"abandoned": "yes", "building": "yes"}),
     0.45, 0.70, {"abandoned"}),
    ("shipwreck",
     lambda: osm_place({"historic": "wreck"}),
     0.45, 0.70, {"shipwreck"}),
    ("church with wikipedia",
     lambda: osm_place({"building": "church", "name": "St Nicholas", "wikipedia": "en:St_Nicholas"}),
     0.0, 0.16, set()),
    ("museum",
     lambda: osm_place({"tourism": "museum", "name": "City Museum", "website": "https://x.org"}),
     0.0, 0.16, set()),
    ("war memorial bust",
     lambda: osm_place({"historic": "memorial", "memorial": "bust", "name": "Petar Lubarda"}),
     0.0, 0.2, set()),
    ("ordinary viewpoint",
     lambda: osm_place({"tourism": "viewpoint"}),
     0.0, 0.2, set()),
    ("ordinary named hilltop",
     lambda: osm_place({"natural": "peak", "name": "Gomila", "ele": "156"}),
     0.0, 0.3, set()),
    ("tourist attraction",
     lambda: osm_place({"tourism": "attraction", "name": "Kanli Kula"}),
     0.0, 0.2, set()),
    ("notable hotel",
     lambda: osm_place({"tourism": "hotel", "stars": "5", "name": "Grand"}),
     0.0, 0.12, set()),
    ("park",
     lambda: plain([C.PARK], name="Central Park"),
     0.0, 0.15, set()),
    ("atlas obscura bunker",
     lambda: plain([C.MILITARY, C.ABANDONED], name="Submarine Base", source=S.ATLAS_OBSCURA,
                   description="Secret cold-war submarine pen", photos=["https://x/y.jpg"]),
     0.80, 1.0, {"listed on Atlas Obscura", "abandoned"}),
    ("famous castle (50 wikipedia languages)",
     lambda: plain([C.LANDMARK, C.MILITARY], name="Famous Fortress",
                   metadata={"sitelinks": 50, "wikidata_id": "Q1"}),
     0.0, 0.35, set()),
    ("unknown blob", lambda: plain([], name=None), 0.0, 0.2, set()),
]


@pytest.mark.parametrize(
    ("make", "low", "high", "reasons"),
    [pytest.param(m, lo, hi, r, id=i) for i, m, lo, hi, r in _BANDS],
)
def test_score_bands_and_reasons(make, low, high, reasons):
    result = score_place(make())
    assert low <= result.score <= high, result
    assert reasons <= set(result.reasons), result.reasons


# ── Relative ordering: the properties the tuning must never break ─────────────


def test_documented_bunker_outranks_unknown_blob():
    bunker = osm_place({"military": "bunker", "name": "Fort Zlatica", "description": "A WWII bunker with tunnels",
                        "abandoned": "yes", "start_date": "1944"})
    blob = osm_place({"abandoned": "yes", "building": "yes"})
    assert score_place(bunker).score > score_place(blob).score


def test_data_richness_breaks_ties_between_equal_places():
    bare = plain([C.RUINS], name=None)
    rich = plain([C.RUINS], name="Old Ruins", description="Ruins of a 13th century chapel on the hill",
                 photos=["https://x/p.jpg"], tags=["a=1", "b=2", "c=3", "d=4"])
    assert score_place(rich).score > score_place(bare).score


def test_unnamed_is_penalised_but_still_returned():
    named = osm_place({"abandoned": "yes", "building": "yes", "name": "Villa Mir"})
    unnamed = osm_place({"abandoned": "yes", "building": "yes"})
    assert score_place(unnamed).score < score_place(named).score
    assert "unnamed" in score_place(unnamed).penalties
    assert score_place(unnamed).score > 0.4


def test_atlas_obscura_listing_is_a_strong_signal():
    base = plain([C.INDUSTRIAL], name="Old Factory")
    atlas = plain([C.INDUSTRIAL], name="Old Factory", source=S.ATLAS_OBSCURA)
    assert score_place(atlas).score >= score_place(base).score + 0.15
    assert score_place(atlas).reasons[0] == "listed on Atlas Obscura"


def test_mainstream_sitelinks_lower_the_score_monotonically():
    def with_links(n):
        return plain([C.MILITARY, C.LANDMARK], name="Fortress", metadata={"sitelinks": n})
    scores = [score_place(with_links(n)).score for n in (0, 3, 7, 15, 25, 60)]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] - scores[-1] > 0.25
    assert any("Wikipedia language editions" in p for p in score_place(with_links(60)).penalties)


def test_wikipedia_article_lowers_and_removes_off_path_reason():
    no_wiki = osm_place({"military": "bunker", "name": "B"})
    wiki = osm_place({"military": "bunker", "name": "B", "wikipedia": "en:B"})
    a, b = score_place(no_wiki), score_place(wiki)
    assert a.score > b.score
    assert "no Wikipedia article — off the tourist path" in a.reasons
    assert "no Wikipedia article — off the tourist path" not in b.reasons


def test_enriched_wikipedia_field_counts_as_having_an_article():
    place = osm_place({"military": "bunker", "name": "B"})
    before = score_place(place).score
    place.wikipedia = {"title": "B", "extract": "...", "url": "https://en.wikipedia.org/wiki/B", "lang": "en"}
    assert score_place(place).score < before


def test_off_path_reason_is_not_given_to_mundane_places():
    assert "no Wikipedia article — off the tourist path" not in score_place(
        osm_place({"tourism": "museum", "name": "M"})).reasons


def test_age_adds_a_reason_and_older_scores_higher():
    def aged(date):
        return osm_place({"historic": "ruins", "name": "R", "start_date": date})
    old, mid, recent, none = (score_place(aged(d)) for d in ("1650", "1880", "1930", "1990"))
    assert old.score > mid.score > recent.score > none.score
    assert "dates from 1650" in old.reasons


def test_restricted_access_is_a_small_signal():
    open_ = score_place(osm_place({"historic": "ruins", "name": "R"}))
    closed = score_place(osm_place({"historic": "ruins", "name": "R", "access": "private"}))
    assert closed.score > open_.score and "restricted access" in closed.reasons
    assert closed.score - open_.score < 0.1


def test_ticketed_commercial_places_lose_points():
    free = score_place(osm_place({"historic": "fort", "name": "F"}))
    paid = score_place(osm_place({"historic": "fort", "name": "F", "fee": "yes",
                                  "opening_hours": "09:00-21:00"}))
    assert paid.score < free.score and "ticketed / commercial" in paid.penalties


def test_lifecycle_beats_same_thing_in_use():
    active = osm_place({"industrial": "factory", "name": "Zavod"})
    dead = osm_place({"industrial": "factory", "name": "Zavod", "abandoned": "yes"})
    assert score_place(dead).score > score_place(active).score + 0.15


def test_second_rare_category_adds_a_little():
    one = plain([C.MILITARY], name="X")
    two = plain([C.MILITARY, C.UNDERGROUND], name="X")
    assert score_place(two).score > score_place(one).score


def test_community_and_deep_research_sources_add_reasons():
    p = plain([C.NATURE_HIDDEN], name="Secret Grotto", source=S.COMMUNITY,
              sources=[S.COMMUNITY, S.DEEP_RESEARCH])
    result = score_place(p)
    assert "added by the community" in result.reasons
    assert "surfaced by deep research" in result.reasons


def test_category_rarity_ordering():
    w = CATEGORY_WEIGHTS
    for rare in (C.ABANDONED, C.UNDERGROUND, C.CAVE, C.RUINS, C.MILITARY, C.INDUSTRIAL):
        for common in (C.MUSEUM, C.RESTAURANT_NOTABLE, C.PARK, C.LANDMARK, C.RELIGIOUS):
            assert w[rare] > w[common], (rare, common)


# ── Pure / deterministic / bounded ───────────────────────────────────────────


@pytest.mark.parametrize("make", [m for _, m, *_ in _BANDS])
def test_score_is_deterministic_and_bounded(make):
    a, b = score_place(make()), score_place(make())
    assert a == b
    assert 0.0 <= a.score <= 1.0
    assert len(a.reasons) <= 5 and len(set(a.reasons)) == len(a.reasons)


def test_scoring_does_not_mutate_the_place():
    place = osm_place({"military": "bunker", "abandoned": "yes", "name": "B"})
    snapshot = place.model_dump()
    score_place(place)
    assert place.model_dump() == snapshot


def test_apply_unusual_scores_sets_fields_and_is_idempotent():
    places = [osm_place({"military": "bunker", "name": "B"}, id=1),
              osm_place({"tourism": "museum", "name": "M"}, id=2)]
    apply_unusual_scores(places)
    first = [(p.unusual_score, list(p.unusual_reasons)) for p in places]
    apply_unusual_scores(places)
    assert [(p.unusual_score, list(p.unusual_reasons)) for p in places] == first
    assert places[0].unusual_score > places[1].unusual_score
    assert places[0].unusual_reasons and places[1].unusual_reasons == []


def test_reasons_do_not_repeat_general_ideas():
    r = score_place(osm_place({"military": "bunker", "abandoned": "yes", "name": "B"})).reasons
    assert "military bunker" in r and "military site" not in r and "underground" not in r
    r = score_place(osm_place({"natural": "cave_entrance"})).reasons
    assert "cave entrance" in r and "cave" not in r
    r = score_place(osm_place({"disused": "yes", "military": "bunker", "name": "B"})).reasons
    assert "disused" in r and "abandoned" not in r


def test_garbage_metadata_never_crashes():
    p = plain([C.RUINS], metadata={"osm_tags": "not-a-dict", "sitelinks": "many",
                                    "start_date": 1914, "access": None})
    assert 0.0 <= score_place(p).score <= 1.0
    p = plain([C.RUINS], metadata={"osm_tags": {"start_date": "???", "wikipedia": ""}, "sitelinks": -3})
    assert 0.0 <= score_place(p).score <= 1.0


def test_sort_key_is_total_and_deterministic():
    a = plain([C.RUINS], name="Alpha", id="a")
    b = plain([C.RUINS], name="Alpha", id="b")
    a.unusual_score = b.unusual_score = 0.5
    a.confidence = b.confidence = 0.5
    assert sorted([b, a], key=unusual_sort_key) == [a, b]
    a.confidence = 0.4
    assert sorted([a, b], key=unusual_sort_key) == [b, a]
    a.unusual_score = 0.9
    assert sorted([b, a], key=unusual_sort_key)[0] is a


# ── Site diversification ─────────────────────────────────────────────────────


def _scored(pid, score, lat, lng, name=None):
    p = plain([C.RUINS], name=name or f"Ruin {pid}", id=pid, lat=lat, lng=lng)
    p.unusual_score = score
    return p


def test_diversify_noop_on_trivial_input():
    assert diversify_by_site([]) == []
    one = [_scored("a", 0.9, 42.0, 18.0)]
    assert diversify_by_site(one) == one


def test_diversify_interleaves_parts_of_one_site_with_other_sites():
    # five buildings of one fort (within ~50 m) all out-score two separate gems
    fort = [_scored(f"f{i}", 0.80 - i * 0.01, 42.4000 + i * 0.0002, 18.5000) for i in range(5)]
    other1 = _scored("g1", 0.70, 42.5000, 18.6000, name="Other Gem")
    other2 = _scored("g2", 0.66, 42.6000, 18.7000, name="Third Gem")
    ranked = sorted([*fort, other1, other2], key=unusual_sort_key)
    assert [p.id for p in ranked[:3]] == ["f0", "f1", "f2"]  # raw order is a wall of one fort
    top3 = [p.id for p in diversify_by_site(ranked)[:3]]
    assert set(top3) == {"f0", "g1", "g2"}  # one per site


def test_diversify_never_drops_or_duplicates():
    ranked = sorted(
        [_scored(f"p{i}", 0.9 - i * 0.02, 42.4 + (i % 4) * 0.0003, 18.5 + (i // 4) * 0.0003) for i in range(30)],
        key=unusual_sort_key,
    )
    out = diversify_by_site(ranked)
    assert sorted(p.id for p in out) == sorted(p.id for p in ranked)


def test_diversify_keeps_unrelated_places_in_score_order():
    ranked = [_scored(f"p{i}", 0.9 - i * 0.1, 42.0 + i * 0.5, 18.0, name=f"Place Number{i}") for i in range(6)]
    assert [p.id for p in diversify_by_site(ranked)] == [p.id for p in ranked]


def test_diversify_groups_a_named_chain_over_kilometres():
    # twelve segments of one abandoned railway, ~800 m apart (9 km in all), vs. two unrelated gems
    line = [_scored(f"seg{i}", 0.75, 42.40 + i * 0.007, 18.5, name="Dalmatinerbahn") for i in range(12)]
    gems = [_scored("g1", 0.70, 43.0, 19.0, name="Cave of Winds"),
            _scored("g2", 0.66, 43.1, 19.1, name="Hidden Bunker Zeta")]
    ranked = sorted([*line, *gems], key=unusual_sort_key)
    top3 = {p.id for p in diversify_by_site(ranked)[:3]}
    assert "g1" in top3 and "g2" in top3
    assert sum(1 for i in top3 if i.startswith("seg")) == 1


def test_diversify_neighbours_sink_but_stay_above_mundane_places():
    swarm = [_scored(f"s{i}", 0.62, 42.4 + i * 0.0003, 18.5, name=None) for i in range(10)]
    for s in swarm:
        s.name = "Abandoned building"
        s.metadata["name_generated"] = True
    museum = _scored("m", 0.10, 43.0, 19.0, name="City Museum")
    out = [p.id for p in diversify_by_site(sorted([*swarm, museum], key=unusual_sort_key))]
    assert out.index("m") == 10  # floor keeps 0.62 * 0.3 above 0.10: museum stays last


def test_diversify_is_deterministic():
    ranked = sorted(
        [_scored(f"p{i}", round(0.9 - i * 0.013, 3), 42.4 + (i % 5) * 0.0004, 18.5 + (i % 3) * 0.0004) for i in range(25)],
        key=unusual_sort_key,
    )
    assert [p.id for p in diversify_by_site(ranked)] == [p.id for p in diversify_by_site(list(ranked))]


def test_diversify_does_not_mutate_inputs():
    ranked = [_scored("a", 0.9, 42.0, 18.0), _scored("b", 0.8, 42.0001, 18.0)]
    before = [(p.id, p.unusual_score) for p in ranked]
    diversify_by_site(ranked)
    assert [(p.id, p.unusual_score) for p in ranked] == before


def test_namesakes_sink_less_than_neighbours():
    leader = _scored("a", 0.8, 42.4, 18.5, name="Fort Zeta")
    namesake = _scored("b", 0.8, 42.5, 18.6, name="Zeta Fort")        # 13 km away, same name
    neighbour = _scored("c", 0.8, 42.4003, 18.5, name="Something Else")  # 33 m away
    stranger = _scored("d", 0.5, 43.5, 19.5, name="Unrelated Gem")
    out = [p.id for p in diversify_by_site([leader, namesake, neighbour, stranger])]
    # namesake keeps 0.8 * 0.8 = 0.64, neighbour drops to 0.8 * 0.6 = 0.48 < stranger 0.5
    assert out == ["a", "b", "d", "c"]


def test_partial_name_overlap_still_groups_a_line():
    segments = [
        _scored("s1", 0.74, 42.40, 18.50, name="Dalmatinerbahn"),
        _scored("s2", 0.74, 42.41, 18.51, name="Stari železnički most (Dalmatinerbahn)"),
    ]
    other = _scored("g", 0.62, 43.0, 19.0, name="Cave of Winds")
    assert [p.id for p in diversify_by_site([*segments, other])] == ["s1", "g", "s2"]


def test_museums_are_penalised_even_with_a_rare_underlying_category():
    mine_museum = osm_place({"tourism": "museum", "historic": "mine", "name": "Kopalnia (skansen)",
                             "fee": "yes", "opening_hours": "Tu-Su 09:00-16:00"})
    real_mine = osm_place({"historic": "mine", "name": "Stara kopalnia"})
    a, b = score_place(mine_museum), score_place(real_mine)
    assert a.score < 0.4 < b.score
    assert "museum (a managed visitor attraction)" in a.penalties
