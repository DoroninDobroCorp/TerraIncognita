"""Story 1.4 — Data Fusion & Deduplication.

Merges places from multiple sources, deduplicates by geo-proximity,
enriches combined entries with data from all matching sources,
and computes a confidence score.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.models.place import Place, PlaceCategory, PlaceSource
from app.utils.geo import haversine_distance_m

logger = logging.getLogger(__name__)


def fuse_places(
    sources_results: list[list[Place]],
    dedup_distance_m: float | None = None,
) -> list[Place]:
    """Merge place lists from multiple sources.

    1. Flatten all places.
    2. Group by geo-proximity (< dedup_distance_m).
    3. Merge grouped items into single Place with enriched data.
    4. Re-compute confidence scores.
    """
    threshold = dedup_distance_m or settings.dedup_distance_meters
    all_places: list[Place] = []
    for batch in sources_results:
        all_places.extend(batch)

    if not all_places:
        return []

    # Greedy clustering by distance
    clusters: list[list[Place]] = []
    used: set[int] = set()

    for i, p in enumerate(all_places):
        if i in used:
            continue
        cluster = [p]
        used.add(i)
        for j in range(i + 1, len(all_places)):
            if j in used:
                continue
            dist = haversine_distance_m(
                p.coordinates.lat, p.coordinates.lng,
                all_places[j].coordinates.lat, all_places[j].coordinates.lng,
            )
            if dist < threshold:
                cluster.append(all_places[j])
                used.add(j)
        clusters.append(cluster)

    merged = [_merge_cluster(c) for c in clusters]
    logger.info(
        "Fusion: %d places → %d clusters → %d merged",
        len(all_places), len(clusters), len(merged),
    )
    return merged


def _merge_cluster(cluster: list[Place]) -> Place:
    """Merge a cluster of nearby places into a single Place."""
    if len(cluster) == 1:
        p = cluster[0]
        p.confidence = _compute_confidence(p, source_count=1)
        return p

    # Primary = the one with the most data (name + description + photos)
    primary = max(cluster, key=lambda p: _richness(p))
    all_sources: set[PlaceSource] = set()
    all_categories: set[PlaceCategory] = set()
    all_tags: set[str] = set()
    all_photos: list[str] = []
    merged_meta: dict = {}

    for p in cluster:
        all_sources.add(p.source)
        all_sources.update(p.sources)
        all_categories.update(p.categories)
        all_tags.update(p.tags)
        all_photos.extend(p.photos)
        merged_meta.update(p.metadata)

        # Prefer the longest name / description
        if p.name and (not primary.name or len(p.name) > len(primary.name)):
            primary.name = p.name
        if p.description and (
            not primary.description or len(p.description) > len(primary.description)
        ):
            primary.description = p.description

    primary.sources = sorted(all_sources, key=lambda s: s.value)
    primary.categories = sorted(all_categories, key=lambda c: c.value)
    primary.tags = sorted(all_tags)
    # Deduplicate photos while preserving order
    seen: set[str] = set()
    unique_photos: list[str] = []
    for url in all_photos:
        if url not in seen:
            seen.add(url)
            unique_photos.append(url)
    primary.photos = unique_photos
    primary.metadata = merged_meta
    primary.confidence = _compute_confidence(primary, source_count=len(all_sources))
    return primary


def _richness(p: Place) -> int:
    score = 0
    if p.name:
        score += 2
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
    return round(min(max(raw, 0.0), 1.0), 3)


def recompute_confidence(places: list[Place]) -> None:
    """Recompute confidence for all places (call after classification)."""
    for p in places:
        p.confidence = _compute_confidence(p, source_count=len(p.sources) or 1)
