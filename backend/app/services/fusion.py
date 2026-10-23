"""Story 1.4 — Data Fusion & Deduplication.

Merges places from multiple sources. Two entries are the same place only when *distance and
identity* agree — distance alone merges neighbouring bunkers, a viewpoint into the church next
to it, a hilltop into a pillbox:

* a shared Wikidata id is decisive (different ids always mean different places);
* distinctive names that match ("Fort Mamula" / "Mamula Fort" / "Tvrđava Mamula") merge up to
  ``dedup_name_match_distance_meters`` apart (a polygon centre vs a pin);
* partially matching names merge within ``dedup_distance_meters`` when the categories agree;
* unnamed / generic places merge only across *different* sources (one source rarely lists an
  object twice), within ``dedup_distance_meters``, when their categories agree — or when their
  coordinates are practically identical.

Merging keeps everything: photos, tags, sources, categories (+ per-category confidence), metadata
(deep-merged), the other names and ids of the cluster, the best description and the highest
unusualness score. Finally a confidence score is computed.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.models.place import Place, PlaceCategory, PlaceSource
from app.services import names
from app.utils.geo import haversine_distance_m

logger = logging.getLogger(__name__)

STRONG_NAME_MATCH = names.STRONG_MATCH
WEAK_NAME_MATCH = names.WEAK_MATCH
SAME_POINT_M = 3.0  # coordinates this close are one object, whatever the names say
WIKIDATA_MATCH_M = 500.0  # same Wikidata id: coordinates of big objects can disagree this much
_MAX_REASONS = 5

# Which source to trust for coordinates (survey-grade first, LLM guesses last).
_COORDINATE_RANK: dict[PlaceSource, int] = {
    PlaceSource.OSM: 0,
    PlaceSource.COMMUNITY: 1,
    PlaceSource.ATLAS_OBSCURA: 2,
    PlaceSource.FLICKR: 3,
    PlaceSource.WIKIDATA: 4,
    PlaceSource.DEEP_RESEARCH: 5,
    PlaceSource.LLM: 6,
}


# ── Matching ───────────────────────────────────────────────────


@dataclass
class _Entry:
    place: Place
    variants: list[frozenset[str]]  # distinctive tokens of each known name; empty = unnamed / generic
    sources: frozenset[PlaceSource]
    wikidata_id: str | None
    x: float = 0.0
    y: float = 0.0


def _wikidata_id(p: Place) -> str | None:
    qid = p.metadata.get("wikidata_id")
    if isinstance(qid, str) and qid:
        return qid
    if p.id.startswith("wiki_Q"):
        return p.id[len("wiki_"):]
    return None


_NAME_METADATA_KEYS = ("name_en", "name_ru", "name_local")


def _name_variants(p: Place) -> list[frozenset[str]]:
    """Distinctive tokens of the name and its known translations / alternates.

    A place called "Вход в катакомбы" (name:en "Catacombs entrance") must still match Atlas
    Obscura's "Odesa Catacombs"; generated names ("Abandoned building") identify nothing.
    """
    if p.metadata.get("name_generated"):
        return []
    candidates: list[Any] = [p.name, *(p.metadata.get(k) for k in _NAME_METADATA_KEYS)]
    alternates = p.metadata.get("alt_names")
    if isinstance(alternates, list):
        candidates += alternates[:6]
    sets = (names.distinctive_tokens(c) for c in candidates if isinstance(c, str))
    return list(dict.fromkeys(t for t in sets if t))


def _make_entry(p: Place) -> _Entry:
    return _Entry(p, _name_variants(p), frozenset({p.source, *p.sources}), _wikidata_id(p))


def _name_similarity(a: _Entry, b: _Entry) -> float:
    return max(
        (names.token_similarity(x, y) for x in a.variants for y in b.variants), default=0.0
    )


def _categories_compatible(a: Place, b: Place) -> bool:
    """Could these be the same kind of thing? LANDMARK / "no category" are wildcards."""
    wildcard = {PlaceCategory.LANDMARK}
    ca, cb = set(a.categories) - wildcard, set(b.categories) - wildcard
    if not ca or not cb:
        return True
    return bool(ca & cb)


def _same_place(a: _Entry, b: _Entry, threshold: float, name_range: float) -> bool:
    d = haversine_distance_m(
        a.place.coordinates.lat, a.place.coordinates.lng,
        b.place.coordinates.lat, b.place.coordinates.lng,
    )
    if a.wikidata_id and b.wikidata_id:
        return a.wikidata_id == b.wikidata_id and d <= WIKIDATA_MATCH_M
    far = max(threshold, name_range)
    if d > far:
        return False

    if a.variants and b.variants:
        sim = _name_similarity(a, b)
        if sim >= STRONG_NAME_MATCH:
            return True
        if d > threshold:
            return False
        if sim >= WEAK_NAME_MATCH:
            return d <= SAME_POINT_M or _categories_compatible(a.place, b.place)
        # Clearly different names: only identical coordinates from different sources
        # (an alternate-language title) can still be one place.
        return d <= SAME_POINT_M and not (a.sources & b.sources)

    # At least one side has no identifying name.
    if d > threshold:
        return False
    if d <= SAME_POINT_M:
        return True
    if a.sources & b.sources:
        return False  # one source does not list the same object twice at two different points
    return _categories_compatible(a.place, b.place)


def _cluster(entries: list[_Entry], threshold: float, name_range: float) -> list[list[_Entry]]:
    """Connected components of the "same place" relation (order independent)."""
    n = len(entries)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    lat0 = math.radians(sum(e.place.coordinates.lat for e in entries) / n)
    for e in entries:
        e.x = e.place.coordinates.lng * 111_320.0 * max(math.cos(lat0), 0.01)
        e.y = e.place.coordinates.lat * 111_320.0

    cell = max(threshold, name_range, WIKIDATA_MATCH_M if any(e.wikidata_id for e in entries) else 0)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, e in enumerate(entries):
        grid.setdefault((int(e.x // cell), int(e.y // cell)), []).append(i)

    for i, e in enumerate(entries):
        cx, cy = int(e.x // cell), int(e.y // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((cx + dx, cy + dy), ()):
                    if j <= i or find(i) == find(j):
                        continue
                    if _same_place(e, entries[j], threshold, name_range):
                        parent[find(j)] = find(i)

    groups: dict[int, list[_Entry]] = {}  # insertion order = order of each group's first member
    for i, e in enumerate(entries):
        groups.setdefault(find(i), []).append(e)
    return list(groups.values())


def fuse_places(
    sources_results: list[list[Place]],
    dedup_distance_m: float | None = None,
    name_match_distance_m: float | None = None,
) -> list[Place]:
    """Merge place lists from multiple sources.

    1. Flatten all places.
    2. Group places that are the same (distance *and* identity, see module docstring).
    3. Merge each group into one Place with the union of the data.
    4. Compute confidence scores.
    """
    threshold = dedup_distance_m or settings.dedup_distance_meters
    name_range = name_match_distance_m or settings.dedup_name_match_distance_meters
    all_places = [p for batch in sources_results for p in batch]
    if not all_places:
        return []

    clusters = _cluster([_make_entry(p) for p in all_places], threshold, name_range)
    merged = [_merge_cluster([e.place for e in c]) for c in clusters]
    logger.info(
        "Fusion: %d places → %d clusters → %d merged",
        len(all_places), len(clusters), len(merged),
    )
    return merged


# ── Merging ────────────────────────────────────────────────────


def _merge_cluster(cluster: list[Place]) -> Place:
    """Merge a cluster of the same place into a single Place, losing nothing useful."""
    if len(cluster) == 1:
        p = cluster[0]
        p.confidence = _compute_confidence(p, source_count=1)
        return p

    # Primary = the one with the most data; members are visited best-first so that on a
    # conflict the richer entry wins.
    ordered = sorted(cluster, key=_richness, reverse=True)  # stable: ties keep input order
    primary = ordered[0]

    all_sources: set[PlaceSource] = set()
    all_categories: set[PlaceCategory] = set()
    all_tags: set[str] = set()
    cat_conf: dict[str, float] = {}
    photos: list[str] = []
    for p in ordered:
        all_sources.add(p.source)
        all_sources.update(p.sources)
        all_categories.update(p.categories)
        all_tags.update(p.tags)
        for key, value in p.category_confidence.items():
            cat_conf[key] = max(cat_conf.get(key, 0.0), value)
        photos.extend(p.photos)

    name, name_generated = _best_name(ordered)
    alt_names = list(dict.fromkeys(
        p.name for p in ordered
        if p.name and p.name != name and not p.metadata.get("name_generated")
    ))

    metadata = _merge_metadata([p.metadata for p in ordered])
    metadata.pop("name_generated", None)
    if name_generated:
        metadata["name_generated"] = True
    if alt_names:
        metadata["alt_names"] = list(dict.fromkeys([*metadata.get("alt_names", []), *alt_names]))
    metadata["merged_ids"] = [p.id for p in ordered if p.id != primary.id]
    if any("_needs_llm_classification" not in p.metadata for p in ordered):
        metadata.pop("_needs_llm_classification", None)  # someone knows what it is

    # Coordinates: survey-grade source first (OSM) rather than whoever is richest.
    anchor = min(ordered, key=lambda p: _COORDINATE_RANK.get(p.source, 9))
    reasons = list(dict.fromkeys(r for p in ordered for r in p.unusual_reasons))[:_MAX_REASONS]

    primary.name = name
    primary.description = max(
        (p.description for p in ordered if p.description), key=len, default=None
    )
    primary.coordinates = anchor.coordinates.model_copy()
    primary.sources = sorted(all_sources, key=lambda s: s.value)
    primary.categories = sorted(all_categories, key=lambda c: c.value)
    primary.category_confidence = cat_conf
    primary.tags = sorted(all_tags)
    primary.photos = list(dict.fromkeys(photos))
    primary.metadata = metadata
    primary.unusual_score = max(p.unusual_score for p in ordered)
    primary.unusual_reasons = reasons
    primary.wikipedia = next((p.wikipedia for p in ordered if p.wikipedia), None)
    primary.created_at = min(p.created_at for p in ordered)
    primary.updated_at = max(p.updated_at for p in ordered)
    primary.confidence = _compute_confidence(primary, source_count=len(all_sources))
    return primary


def _best_name(ordered: list[Place]) -> tuple[str | None, bool]:
    """(name, is_generated). A real, identifying name beats a generic one beats a generated one."""
    real = [p.name for p in ordered if p.name and not p.metadata.get("name_generated")]
    distinctive = [n for n in real if not names.is_generic_name(n)]
    for pool in (distinctive, real):
        if pool:
            return max(pool, key=len), False  # prefer the longest ("Old Bunker" over "Bunker")
    generated = [p.name for p in ordered if p.name]
    return (generated[0], True) if generated else (None, False)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _merge_metadata(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Deep-merge metadata dicts; earlier entries win conflicts, nothing is dropped."""
    merged: dict[str, Any] = {}
    for meta in parts:
        for key, value in meta.items():
            current = merged.get(key)
            if key not in merged or _is_empty(current):
                merged[key] = _copy(value)
            elif isinstance(current, dict) and isinstance(value, dict):
                merged[key] = {**value, **current}
            elif isinstance(current, list) and isinstance(value, list):
                merged[key] = current + [v for v in value if v not in current]
    return merged


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def _richness(p: Place) -> int:
    score = 0
    if p.name and not p.metadata.get("name_generated"):
        score += 2
    elif p.name:
        score += 1
    if p.description:
        score += 2
    if p.photos:
        score += len(p.photos)
    if p.categories:
        score += 1
    return score


def _compute_confidence(p: Place, source_count: int) -> float:
    """Confidence score combining source diversity, data richness, and category signals.

    Components (weights sum to 1.0):
      0.20 * source_score      — more sources = higher trust
      0.15 * has_photos         — visual evidence
      0.15 * has_desc           — textual evidence
      0.10 * freshness          — data freshness (static for now)
      0.15 * tag_richness       — richer OSM tags = better-documented place
      0.25 * category_conf      — classifier confidence in categorisation
    A place that only has a generated name ("Abandoned building") loses 0.05 so a documented
    place outranks it on otherwise equal evidence.
    """
    source_score = min(source_count / 3.0, 1.0)
    has_photos = 1.0 if p.photos else 0.0
    has_desc = 1.0 if p.description else 0.0
    freshness = 0.8

    # Tag richness: 5+ tags = full score
    tag_richness = min(len(p.tags) / 5.0, 1.0) if p.tags else 0.0

    # Best category confidence (from classifier)
    cat_conf = max(p.category_confidence.values()) if p.category_confidence else 0.3

    raw = (
        0.20 * source_score
        + 0.15 * has_photos
        + 0.15 * has_desc
        + 0.10 * freshness
        + 0.15 * tag_richness
        + 0.25 * cat_conf
    )
    if p.metadata.get("name_generated"):
        raw -= 0.05
    return round(min(max(raw, 0.0), 1.0), 3)


def recompute_confidence(places: list[Place]) -> None:
    """Recompute confidence for all places (call after classification)."""
    for p in places:
        p.confidence = _compute_confidence(p, source_count=len(p.sources) or 1)
