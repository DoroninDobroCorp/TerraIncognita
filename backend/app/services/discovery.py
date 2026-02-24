"""Discovery Engine orchestrator.

Coordinates all sources, applies fusion, classification,
filtering and sorting to produce the final result set.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time

from app.models.place import (
    DiscoverRequest,
    DiscoverResponse,
    Place,
)
from app.services.classifier import classify_places
from app.services.fusion import fuse_places
from app.sources.atlas_obscura import AtlasObscuraSource
from app.sources.deep_research import DeepResearchSource
from app.sources.osm import OSMSource
from app.sources.wikidata import WikidataSource
from app.utils.geo import haversine_distance_m

logger = logging.getLogger(__name__)

# Singleton source instances
_osm = OSMSource()
_atlas = AtlasObscuraSource()
_wiki = WikidataSource()
_deep = DeepResearchSource()


async def discover(req: DiscoverRequest) -> DiscoverResponse:
    """Main discovery pipeline.

    1. Query all sources concurrently.
    2. Fuse & deduplicate.
    3. Classify uncategorised places.
    4. Filter by requested categories & exclusions.
    5. Sort and paginate.
    """
    t0 = time.monotonic()

    # 1. Concurrent source queries
    osm_task = asyncio.create_task(_safe_search(_osm, req.lat, req.lng, req.radius_km))
    atlas_task = asyncio.create_task(_safe_search(_atlas, req.lat, req.lng, req.radius_km))
    wiki_task = asyncio.create_task(_safe_search(_wiki, req.lat, req.lng, req.radius_km))
    deep_task = asyncio.create_task(_safe_search(_deep, req.lat, req.lng, req.radius_km))

    results = await asyncio.gather(osm_task, atlas_task, wiki_task, deep_task)

    # 2. Fuse
    merged = fuse_places(list(results))

    # 3. Classify
    merged = classify_places(merged)

    # 3b. Recompute confidence after classification (uses category_confidence)
    from app.services.fusion import recompute_confidence
    recompute_confidence(merged)

    # 4. Filter
    if req.categories:
        cat_set = set(req.categories)
        merged = [p for p in merged if set(p.categories) & cat_set]

    if req.exclude_visited:
        excluded = set(req.exclude_visited)
        merged = [p for p in merged if p.id not in excluded]

    total = len(merged)

    # 5. Compute distance from request point
    for p in merged:
        p.distance_m = round(
            haversine_distance_m(req.lat, req.lng, p.coordinates.lat, p.coordinates.lng), 1
        )

    # 6. Sort
    merged = _sort_places(merged, req.sort_by, req.lat, req.lng)

    # 7. Pagination (cursor-based)
    start_idx = _decode_cursor(req.cursor)
    page = merged[start_idx : start_idx + req.limit]
    has_more = start_idx + req.limit < total
    next_cursor = _encode_cursor(start_idx + req.limit) if has_more else None

    elapsed = time.monotonic() - t0
    logger.info(
        "Discovery completed: %d places in %.2fs (sources: osm=%d, atlas=%d, wiki=%d, deep=%d)",
        total, elapsed,
        len(results[0]), len(results[1]), len(results[2]), len(results[3]),
    )

    # Get Deep Research status
    dr_status, dr_message = await _deep.get_status(req.lat, req.lng, req.radius_km)

    return DiscoverResponse(
        places=page,
        total=total,
        has_more=has_more,
        cursor=next_cursor,
        deep_research_status=dr_status,
        deep_research_message=dr_message,
    )


async def _safe_search(source, lat: float, lng: float, radius_km: float) -> list[Place]:
    """Run a source search with error isolation."""
    try:
        return await source.search(lat, lng, radius_km)
    except Exception:
        logger.exception("Source %s failed", source.source_name)
        return []


def _sort_places(
    places: list[Place], sort_by: str, lat: float, lng: float
) -> list[Place]:
    if sort_by == "distance":
        return sorted(
            places,
            key=lambda p: haversine_distance_m(lat, lng, p.coordinates.lat, p.coordinates.lng),
        )
    if sort_by == "name":
        return sorted(places, key=lambda p: (p.name or "").lower())
    # Default: confidence descending
    return sorted(places, key=lambda p: p.confidence, reverse=True)


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor).decode())
    except (ValueError, Exception):
        return 0
