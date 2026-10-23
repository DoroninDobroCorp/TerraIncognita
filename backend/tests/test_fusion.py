"""Tests for Story 1.4 — Data Fusion & Deduplication."""

from __future__ import annotations

import itertools
import math
import time
from datetime import UTC, datetime

import pytest

from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource, WikipediaInfo
from app.services.fusion import _compute_confidence, fuse_places


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
        # source 0.20*1 + photos 0.15 + desc 0.15 + fresh 0.10*0.8 + tags 0 + cat 0.25*0.3 (default)
        assert conf == pytest.approx(0.655, abs=0.01)

    def test_confidence_single_source_no_data(self):
        p = _make_place("a")
        conf = _compute_confidence(p, source_count=1)
        # source 0.20/3 + fresh 0.10*0.8 + cat 0.25*0.3 (default)
        assert conf == pytest.approx(0.222, abs=0.01)

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


# ── Distance AND identity: name-aware deduplication ──────────────────────────

M_PER_DEG_LAT = 111_320.0


def at(north_m: float = 0.0, east_m: float = 0.0, lat: float = 42.45, lng: float = 18.53):
    """Coordinates offset from a base point by metres."""
    return (lat + north_m / M_PER_DEG_LAT,
            lng + east_m / (M_PER_DEG_LAT * math.cos(math.radians(lat))))


def make(pid, name=None, *, north=0.0, east=0.0, source=PlaceSource.OSM, categories=(),
         **kw) -> Place:
    lat, lng = at(north, east)
    return Place(
        id=pid, source=source, sources=[source], name=name, categories=list(categories),
        coordinates=Coordinates(lat=lat, lng=lng), **kw,
    )


def clusters(result: list[Place]) -> set[frozenset[str]]:
    """Which ids ended up together (primary id + merged ids)."""
    return {frozenset({p.id, *p.metadata.get("merged_ids", [])}) for p in result}


MIL = PlaceCategory.MILITARY
UND = PlaceCategory.UNDERGROUND
VIEW = PlaceCategory.VIEWPOINT
REL = PlaceCategory.RELIGIOUS


class TestNameAwareDedup:
    def test_same_fort_with_reordered_name_is_merged(self):
        a = make("osm_node_1", "Fort Mamula", north=0)
        b = make("osm_way_2", "Mamula Fort", north=20)
        result = fuse_places([[a, b]])
        assert len(result) == 1

    def test_translated_and_transliterated_names_merge(self):
        for other in ("Tvrđava Mamula", "Форт Мамула", "Mamula"):
            result = fuse_places([[make("a", "Fort Mamula"), make("b", other, east=40)]])
            assert len(result) == 1, other

    def test_two_unnamed_bunkers_30m_apart_stay_separate(self):
        a = make("b1", categories=[MIL, UND])
        b = make("b2", north=30, categories=[MIL, UND])
        assert len(fuse_places([[a, b]])) == 2

    def test_two_generic_named_bunkers_30m_apart_stay_separate(self):
        a = make("b1", "Bunker", categories=[MIL])
        b = make("b2", "Old Bunker", north=30, categories=[MIL])
        assert len(fuse_places([[a, b]])) == 2

    def test_differently_named_bunkers_30m_apart_stay_separate(self):
        a = make("b1", "Bunker Alfa", categories=[MIL])
        b = make("b2", "Bunker Beta", north=30, categories=[MIL])
        assert len(fuse_places([[a, b]])) == 2

    def test_numbered_bunkers_are_different_places(self):
        a = make("b1", "Bunker Zlatica 1", categories=[MIL])
        b = make("b2", "Bunker Zlatica 2", north=25, categories=[MIL])
        assert len(fuse_places([[a, b]])) == 2

    def test_same_name_merges_up_to_name_match_distance(self):
        near = fuse_places([[make("a", "Fort Arza"), make("b", "Fort Arza", north=140)]])
        far = fuse_places([[make("a", "Fort Arza"), make("b", "Fort Arza", north=170)]])
        assert len(near) == 1 and len(far) == 2

    def test_name_match_distance_is_configurable(self):
        a, b = make("a", "Fort Arza"), make("b", "Fort Arza", north=300)
        assert len(fuse_places([[a, b]])) == 2
        a, b = make("a", "Fort Arza"), make("b", "Fort Arza", north=300)
        assert len(fuse_places([[a, b]], name_match_distance_m=400)) == 1

    def test_unnamed_osm_bunker_merges_with_atlas_entry_nearby(self):
        osm = make("osm_node_1", categories=[MIL, UND])
        atlas = make("atlas_1", "Submarine Base", north=30, source=PlaceSource.ATLAS_OBSCURA,
                     categories=[MIL])
        result = fuse_places([[osm], [atlas]])
        assert len(result) == 1 and result[0].name == "Submarine Base"

    def test_viewpoint_next_to_church_is_not_the_church(self):
        view = make("osm_1", categories=[VIEW])
        church = make("wiki_1", "Church of St Nicholas", north=20, source=PlaceSource.WIKIDATA,
                      categories=[REL])
        assert len(fuse_places([[view], [church]])) == 2

    def test_peak_does_not_swallow_a_bunker_on_it(self):
        peak = make("osm_peak", "Gomila", categories=[VIEW, PlaceCategory.NATURE_HIDDEN])
        bunker = make("osm_bunker", north=15, source=PlaceSource.ATLAS_OBSCURA, categories=[MIL])
        assert len(fuse_places([[peak], [bunker]])) == 2

    def test_unnamed_places_of_one_source_do_not_merge_unless_same_point(self):
        a, b = make("a", categories=[MIL]), make("b", north=10, categories=[MIL])
        assert len(fuse_places([[a, b]])) == 2
        a, b = make("a", categories=[MIL]), make("b", north=1, categories=[UND])
        assert len(fuse_places([[a, b]])) == 1  # practically the same point

    def test_same_point_different_names_same_source_stay_separate(self):
        a, b = make("a", "Cafe Luna"), make("b", "Museum of Salt", north=1)
        assert len(fuse_places([[a, b]])) == 2

    def test_same_point_different_names_different_sources_merge(self):
        a = make("a", "Hram Svetog Save")
        b = make("b", "Cathedral of Saint Sava", north=1, source=PlaceSource.ATLAS_OBSCURA)
        assert len(fuse_places([[a], [b]])) == 1

    def test_shared_wikidata_id_merges_despite_distance_and_name(self):
        osm = make("osm_1", "Tvrđava", metadata={"wikidata_id": "Q42"})
        wiki = make("wiki_Q42", "Fortress of Something", north=350, source=PlaceSource.WIKIDATA)
        result = fuse_places([[osm], [wiki]])
        assert len(result) == 1
        assert result[0].metadata["wikidata_id"] == "Q42"

    def test_different_wikidata_ids_never_merge(self):
        a = make("wiki_Q1", "Fort Arza", source=PlaceSource.WIKIDATA)
        b = make("wiki_Q2", "Fort Arza", north=1, source=PlaceSource.WIKIDATA)
        assert len(fuse_places([[a, b]])) == 2
        c = make("osm_1", "Fort Arza", metadata={"wikidata_id": "Q3"})
        assert len(fuse_places([[a], [c]])) == 2

    def test_transitive_chain_of_same_name_is_one_place(self):
        parts = [make(f"p{i}", n, north=i * 100)
                 for i, n in enumerate(["Fort Arza", "Arza Fort", "Fort Arza Lustica"])]
        assert len(fuse_places([parts])) == 1

    def test_result_does_not_depend_on_input_order(self):
        items = [
            ("a", "Fort Mamula", 0, PlaceSource.OSM, [MIL]),
            ("b", "Mamula Fort", 25, PlaceSource.OSM, [MIL]),
            ("c", None, 40, PlaceSource.ATLAS_OBSCURA, [MIL]),
            ("d", "Bunker Alfa", 300, PlaceSource.OSM, [MIL]),
            ("e", "Bunker Beta", 330, PlaceSource.OSM, [MIL]),
            ("f", None, 335, PlaceSource.WIKIDATA, [MIL]),
        ]
        seen = set()
        for perm in itertools.islice(itertools.permutations(items), 0, None, 7):  # ~100 of 720 orders
            places = [make(i, n, north=nm, source=s, categories=c) for i, n, nm, s, c in perm]
            seen.add(frozenset(clusters(fuse_places([places]))))
        assert len(seen) == 1

    def test_no_place_is_lost_or_duplicated(self):
        places = [make(f"p{i}", f"Place {i % 7}", north=(i * 37) % 400, east=(i * 53) % 400,
                       categories=[MIL]) for i in range(60)]
        result = fuse_places([places])
        ids = [i for c in clusters(result) for i in c]
        assert sorted(ids) == sorted(p.id for p in places)

    def test_scales_to_thousands_of_places(self):
        places = [make(f"p{i}", f"Unique Name {i}", north=(i // 60) * 90, east=(i % 60) * 90)
                  for i in range(3000)]
        t0 = time.monotonic()
        result = fuse_places([places])
        assert len(result) == 3000
        assert time.monotonic() - t0 < 10.0  # ~0.1 s on an idle machine; generous for shared CI


class TestMergeKeepsEverything:
    def merged(self, *places, **kw) -> Place:
        result = fuse_places([list(places)], **kw)
        assert len(result) == 1
        return result[0]

    def test_photos_tags_sources_categories_union(self):
        a = make("osm_1", "Fort Arza", photos=["u1", "u2"], tags=["historic=fort"],
                 categories=[PlaceCategory.LANDMARK])
        b = make("atlas_1", "Fort Arza", east=10, source=PlaceSource.ATLAS_OBSCURA,
                 photos=["u2", "u3"], tags=["fort"], categories=[MIL])
        m = self.merged(a, b)
        assert m.photos == ["u1", "u2", "u3"]
        assert m.tags == ["fort", "historic=fort"]
        assert set(m.sources) == {PlaceSource.OSM, PlaceSource.ATLAS_OBSCURA}
        assert set(m.categories) == {PlaceCategory.LANDMARK, MIL}

    def test_category_confidence_takes_the_max_per_category(self):
        a = make("a", "Fort Arza", categories=[MIL], category_confidence={"military": 0.6, "landmark": 0.3})
        b = make("b", "Fort Arza", east=5, categories=[MIL], category_confidence={"military": 0.9})
        m = self.merged(a, b)
        assert m.category_confidence == {"military": 0.9, "landmark": 0.3}

    def test_metadata_is_deep_merged_not_overwritten(self):
        a = make("osm_1", "Fort Arza", metadata={
            "osm_tags": {"historic": "fort", "name": "Fort Arza"}, "access": "no", "list": [1, 2],
            "empty": "", "wikidata_id": "Q9"})
        b = make("atlas_1", "Fort Arza", east=5, source=PlaceSource.ATLAS_OBSCURA, metadata={
            "atlas_url": "/places/arza", "osm_tags": {"historic": "castle", "ele": "40"},
            "access": "yes", "list": [2, 3], "empty": "filled"})
        m = self.merged(a, b).metadata
        assert m["atlas_url"] == "/places/arza"
        assert m["osm_tags"] == {"historic": "fort", "name": "Fort Arza", "ele": "40"}
        assert m["access"] in {"no", "yes"}
        assert sorted(m["list"]) == [1, 2, 3]
        assert m["empty"] == "filled"
        assert m["wikidata_id"] == "Q9"

    def test_richer_entry_wins_conflicts(self):
        poor = make("osm_1", "Fort Arza", metadata={"k": "poor"})
        rich = make("atlas_1", "Fort Arza", east=5, source=PlaceSource.ATLAS_OBSCURA,
                    description="d", photos=["x"], metadata={"k": "rich"})
        m = self.merged(poor, rich)
        # data conflicts go to the richer entry; the id stays with the best-ranked source (OSM)
        assert m.metadata["k"] == "rich" and m.id == "osm_1"
        assert m.metadata["merged_ids"] == ["atlas_1"]

    def test_best_description_is_the_longest(self):
        a = make("a", "Fort Arza", description="Short")
        b = make("b", "Fort Arza", east=5, description="A much longer and more useful description")
        assert self.merged(a, b).description == "A much longer and more useful description"

    def test_distinctive_name_beats_generic_even_if_shorter(self):
        a = make("a", "Old Abandoned Bunker Complex")
        b = make("b", "Zlatica", east=2, source=PlaceSource.ATLAS_OBSCURA)
        m = self.merged(a, b)
        assert m.name == "Zlatica"
        assert m.metadata["alt_names"] == ["Old Abandoned Bunker Complex"]

    def test_real_name_beats_generated_name_even_if_longer(self):
        gen = make("osm_1", "Abandoned military bunker", metadata={"name_generated": True})
        real = make("atlas_1", "Zlatica", east=2, source=PlaceSource.ATLAS_OBSCURA)
        m = self.merged(gen, real)
        assert m.name == "Zlatica" and "name_generated" not in m.metadata

    def test_generated_name_is_kept_and_flagged_when_nothing_better(self):
        a = make("osm_1", "Bunker", metadata={"name_generated": True})
        b = make("osm_2", None, east=1)
        m = self.merged(a, b)
        assert m.name == "Bunker" and m.metadata["name_generated"] is True

    def test_merged_ids_and_alt_names_recorded(self):
        a = make("osm_1", "Fort Mamula", photos=["p"])
        b = make("wiki_Q1", "Mamula Fort", east=5, source=PlaceSource.WIKIDATA)
        m = self.merged(a, b)
        assert m.id == "osm_1"
        assert m.metadata["merged_ids"] == ["wiki_Q1"]
        assert m.metadata["alt_names"] == ["Mamula Fort"]
        assert m.name == "Fort Mamula"

    def test_max_unusual_score_and_reasons_union(self):
        a = make("a", "Fort Arza", unusual_score=0.4, unusual_reasons=["ruins"])
        b = make("b", "Fort Arza", east=5, unusual_score=0.8, unusual_reasons=["abandoned", "ruins"])
        m = self.merged(a, b)
        assert m.unusual_score == 0.8
        assert m.unusual_reasons == ["abandoned", "ruins"] or m.unusual_reasons == ["ruins", "abandoned"]
        assert len(m.unusual_reasons) == 2

    def test_wikipedia_summary_survives_merge(self):
        wiki = WikipediaInfo(title="Fort Arza", extract="e", url="https://en.wikipedia.org/wiki/Fort", lang="en")
        a = make("a", "Fort Arza", description="long description wins primary")
        b = make("b", "Fort Arza", east=5, wikipedia=wiki)
        assert self.merged(a, b).wikipedia == wiki

    def test_coordinates_prefer_osm_over_llm_guess(self):
        llm = make("dr_1", "Fort Arza", source=PlaceSource.DEEP_RESEARCH, description="rich " * 10,
                   photos=["p"])
        osm = make("osm_1", "Fort Arza", north=12)
        m = self.merged(llm, osm)
        assert m.description.startswith("rich")  # richest entry supplies the content
        assert m.id == "osm_1"  # the best-ranked source owns the id...
        assert m.coordinates == osm.coordinates  # ...and the coordinates

    def test_confidence_counts_all_sources(self):
        single = make("a", "Fort Arza", description="d", photos=["p"])
        solo = fuse_places([[single]])[0].confidence
        a = make("a", "Fort Arza", description="d", photos=["p"])
        b = make("b", "Fort Arza", east=2, source=PlaceSource.ATLAS_OBSCURA)
        c = make("c", "Fort Arza", east=3, source=PlaceSource.WIKIDATA)
        assert self.merged(a, b, c).confidence > solo

    def test_timestamps_span_the_cluster(self):
        old, new = datetime(2020, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)
        a = make("a", "Fort Arza", created_at=old, updated_at=old)
        b = make("b", "Fort Arza", east=2, created_at=new, updated_at=new)
        m = self.merged(a, b)
        assert m.created_at == old and m.updated_at == new

    def test_llm_flag_cleared_when_any_member_is_classified(self):
        a = make("a", "Fort Arza", metadata={"_needs_llm_classification": True})
        b = make("b", "Fort Arza", east=2)
        assert "_needs_llm_classification" not in self.merged(a, b).metadata

    def test_single_place_gets_confidence_only(self):
        p = make("solo", "Fort Arza", unusual_score=0.5, tags=["a=b"])
        out = fuse_places([[p]])[0]
        assert out.id == "solo" and out.unusual_score == 0.5 and "merged_ids" not in out.metadata

    def test_generated_name_lowers_confidence(self):
        named = make("a", "Zlatica", description="d")
        generated = make("b", "Bunker", description="d", metadata={"name_generated": True}, north=999)
        n, g = fuse_places([[named, generated]])
        assert g.confidence < n.confidence


class TestNameVariants:
    CAVE = PlaceCategory.CAVE

    def test_translated_name_in_metadata_bridges_scripts(self):
        osm = make("osm_1", "Вход в катакомбы", categories=[self.CAVE],
                   metadata={"name_en": "Catacombs entrance"})
        atlas = make("atlas_1", "Odesa Catacombs", north=9, source=PlaceSource.ATLAS_OBSCURA,
                     categories=[UND, self.CAVE])
        assert len(fuse_places([[osm], [atlas]])) == 1

    def test_negative_control_without_the_translation_they_stay_apart(self):
        osm = make("osm_1", "Вход в катакомбы", categories=[self.CAVE])
        atlas = make("atlas_1", "Odesa Catacombs", north=9, source=PlaceSource.ATLAS_OBSCURA,
                     categories=[UND, self.CAVE])
        assert len(fuse_places([[osm], [atlas]])) == 2

    def test_alt_names_count_too(self):
        osm = make("osm_1", "Tvrđava", metadata={"alt_names": ["Fort Arza"]})
        other = make("wiki_1", "Arza Fort", north=60, source=PlaceSource.WIKIDATA)
        assert len(fuse_places([[osm], [other]])) == 1

    def test_generated_names_are_never_used_for_matching(self):
        a = make("osm_1", "Abandoned building", metadata={"name_generated": True,
                                                         "name_en": "Abandoned building"})
        b = make("osm_2", "Abandoned building", north=20, metadata={"name_generated": True})
        assert len(fuse_places([[a, b]])) == 2

    def test_merged_place_keeps_every_name(self):
        osm = make("osm_1", "Вход в катакомбы", metadata={"name_en": "Catacombs entrance"})
        atlas = make("atlas_1", "Odesa Catacombs", north=2, source=PlaceSource.ATLAS_OBSCURA)
        [m] = fuse_places([[osm], [atlas]])
        assert m.metadata["name_en"] == "Catacombs entrance"
        assert {m.name, *m.metadata["alt_names"]} == {"Вход в катакомбы", "Odesa Catacombs"}


class TestClusterGuards:
    """Pairwise rules are sound; connected components must not undo them via a bridging entry."""

    def test_a_bridge_cannot_merge_two_different_wikidata_ids(self):
        # a distinctive name ("Mamula"): generic words would make these entries "unnamed" and the other
        # guard would block the merge, hiding a broken Wikidata guard
        fortress = make("wiki_Q1", "Fort Mamula", source=PlaceSource.WIKIDATA, categories=[MIL],
                        metadata={"wikidata_id": "Q1"})
        island = make("wiki_Q2", "Mamula Island Fort", east=20, source=PlaceSource.WIKIDATA, categories=[MIL],
                      metadata={"wikidata_id": "Q2"})
        bridge = make("osm_1", "Fort Mamula", east=10, categories=[MIL])  # no wikidata tag: matches both
        result = fuse_places([[fortress, island], [bridge]])
        ids = [{p.id, *p.metadata.get("merged_ids", [])} for p in result]
        assert not any({"wiki_Q1", "wiki_Q2"} <= group for group in ids), ids
        assert any("osm_1" in group for group in ids)  # the bridge joined one of them, not both

    def test_the_guard_also_tracks_ids_through_chains_of_merges(self):
        """Q1 absorbs the bridge first; a later Q2 must still be refused through the merged cluster."""
        parts = [
            make("wiki_Q1", "Fort Mamula", source=PlaceSource.WIKIDATA, categories=[MIL], metadata={"wikidata_id": "Q1"}),
            make("osm_1", "Fort Mamula", east=10, categories=[MIL]),
            make("osm_2", "Mamula Fort", east=12, categories=[MIL]),
            make("wiki_Q2", "Fort Mamula", east=15, source=PlaceSource.WIKIDATA, categories=[MIL], metadata={"wikidata_id": "Q2"}),
        ]
        for order in (parts, parts[::-1], [parts[3], parts[1], parts[0], parts[2]]):
            result = fuse_places([order])
            groups = [{p.id, *p.metadata.get("merged_ids", [])} for p in result]
            assert not any({"wiki_Q1", "wiki_Q2"} <= g for g in groups), groups

    def test_a_bridge_cannot_merge_two_unnamed_entries_of_one_source(self):
        a = make("osm_node_10", None, north=0, categories=[MIL])
        b = make("osm_node_11", None, north=80, categories=[MIL])
        bridge = make("atlas_5", None, north=40, source=PlaceSource.ATLAS_OBSCURA, categories=[MIL])
        result = fuse_places([[a, b], [bridge]], dedup_distance_m=50)
        groups = clusters(result)
        assert not any({"osm_node_10", "osm_node_11"} <= g for g in groups), groups

    def test_unnamed_duplicates_at_the_same_point_still_merge(self):
        node = make("osm_node_1", None, categories=[MIL])
        way = make("osm_way_1", None, north=1, categories=[MIL])  # centre of the same bunker
        assert len(fuse_places([[node, way]])) == 1

    def test_a_legitimate_three_way_merge_is_unaffected(self):
        osm = make("osm_1", "Fort Arza", categories=[MIL])
        atlas = make("atlas_1", "Arza Fort", east=10, source=PlaceSource.ATLAS_OBSCURA, categories=[MIL])
        wiki = make("wiki_Q5", "Tvrđava Arza", east=15, source=PlaceSource.WIKIDATA, categories=[MIL],
                    metadata={"wikidata_id": "Q5"})
        assert len(fuse_places([[osm], [atlas], [wiki]])) == 1


class TestStableIdentity:
    def _fort(self, source, pid, name, **kw):
        return make(pid, name, source=source, categories=[MIL], **kw)

    def test_the_id_does_not_depend_on_which_sources_answered(self):
        osm = self._fort(PlaceSource.OSM, "osm_node_1", "Fort Mamula")
        wiki = self._fort(PlaceSource.WIKIDATA, "wiki_Q123", "Tvrđava Mamula", east=5,
                          description="Fortress at the Bay of Kotor", photos=["p.jpg"],
                          metadata={"wikidata_id": "Q123"})
        with_wiki = fuse_places([[osm], [wiki]])
        assert [p.id for p in with_wiki] == ["osm_node_1"]
        assert with_wiki[0].metadata["merged_ids"] == ["wiki_Q123"]
        # ...while the richer entry still supplies the data
        assert with_wiki[0].name == "Tvrđava Mamula" or with_wiki[0].description
        assert [p.id for p in fuse_places([[osm]])] == ["osm_node_1"]

    def test_output_is_fully_independent_of_input_order(self):
        items = [
            ("osm_1", "Fort Mamula", 0, PlaceSource.OSM),
            ("osm_2", "Mamula Fort", 25, PlaceSource.OSM),  # same richness as osm_1: a tie
            ("atlas_1", None, 40, PlaceSource.ATLAS_OBSCURA),
        ]
        outputs = set()
        for perm in itertools.permutations(items):
            places = [make(i, n, north=nm, source=s, categories=[MIL]) for i, n, nm, s in perm]
            outputs.add(tuple(
                (p.id, p.name, tuple(p.metadata.get("alt_names", [])), tuple(p.metadata.get("merged_ids", [])))
                for p in fuse_places([places])
            ))
        assert len(outputs) == 1, outputs
