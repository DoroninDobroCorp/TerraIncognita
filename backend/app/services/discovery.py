"""Discovery Engine orchestrator.

Coordinates all sources, applies fusion, classification, unusualness scoring,
filtering and sorting to produce the final result set.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time

import httpx

from app.config import settings
from app.models.place import (
    DiscoverRequest,
    DiscoverResponse,
    Place,
)
from app.services.classifier import classify_places
from app.services.fusion import fuse_places, recompute_confidence
from app.services.scoring import apply_unusual_scores, diversify_by_site, unusual_sort_key
from app.sources.atlas_obscura import AtlasObscuraSource
from app.sources.base import PartialSourceResult, SourceError
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

# Outcome of one source query, reported in DiscoverResponse.source_status.
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"  # answered, but part of the query failed
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"


async def discover(req: DiscoverRequest) -> DiscoverResponse:
    """Main discovery pipeline.

    1. Query all sources concurrently (each isolated: a failing source never fails the request).
    2. Fuse & deduplicate.
    3. Classify uncategorised places, recompute confidence.
    4. Score unusualness.
    5. Filter by requested categories & exclusions.
    6. Sort and paginate.
    """
    t0 = time.monotonic()

    # 1. Concurrent source queries. Looked up at call time so tests can swap the singletons.
    sources = (("osm", _osm), ("atlas", _atlas), ("wiki", _wiki), ("deep_research", _deep))
    outcomes = await _gather_sources(sources, req)
    results = [places for places, _ in outcomes]
    source_status = {name: status for (name, _), (_, status) in zip(sources, outcomes, strict=True)}

    # 2. Fuse
    merged = fuse_places(results)

    # 3. Classify
    merged = classify_places(merged)

    # 3b. Recompute confidence after classification (uses category_confidence)
    recompute_confidence(merged)

    # 3c. Unusualness: needs the final (merged, classified) place to see all evidence
    apply_unusual_scores(merged)

    # 4. Filter
    if req.categories:
        cat_set = set(req.categories)
        merged = [p for p in merged if set(p.categories) & cat_set]

    if req.exclude_visited:
        excluded = set(req.exclude_visited)
        # A merged place keeps the ids of everything that was folded into it.
        merged = [
            p for p in merged
            if p.id not in excluded and not excluded.intersection(p.metadata.get("merged_ids", ()))
        ]

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
        "Discovery completed: %d places in %.2fs (sources: %s)",
        total, elapsed,
        ", ".join(f"{name}={len(r)}/{source_status[name]}" for (name, _), r in zip(sources, results, strict=True)),
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
        source_status=source_status,
    )


# Sources that missed the response deadline keep running here so their results land in the
# disk caches; the next request for the same area is then fast. Strong refs: the loop only
# keeps weak ones, and a garbage-collected task would be cancelled mid-flight.
_background_tasks: set[asyncio.Task] = set()


async def _gather_sources(sources, req: DiscoverRequest) -> list[tuple[list[Place], str]]:
    """Query all sources concurrently, but don't let the slowest one hold the answer hostage.

    Waits up to ``discovery_deadline_seconds`` for everyone. If that passes and at least one
    source has produced places, those are returned and the stragglers are reported as
    ``timeout`` while they finish in the background. If nothing has produced places yet we
    keep waiting for the first source that does (or for all of them to give up) — an empty
    answer helps nobody — but never past ``discovery_max_wait_seconds``, which stays below
    the client's own timeout.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    tasks = [
        asyncio.ensure_future(_query_source(name, src, req.lat, req.lng, req.radius_km))
        for name, src in sources
    ]
    _, pending = await asyncio.wait(tasks, timeout=settings.discovery_deadline_seconds)
    while pending and not any(t.done() and t.result()[0] for t in tasks):
        remaining = started + settings.discovery_max_wait_seconds - loop.time()
        if remaining <= 0:
            break
        _, pending = await asyncio.wait(
            pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
    outcomes: list[tuple[list[Place], str]] = []
    for task in tasks:
        if task in pending:
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
            outcomes.append(([], STATUS_TIMEOUT))
        else:
            outcomes.append(task.result())
    if pending:
        logger.info("Answering without %d slow source(s); they continue in the background", len(pending))
    return outcomes


async def _query_source(
    name: str, source, lat: float, lng: float, radius_km: float
) -> tuple[list[Place], str]:
    """Run one source with error isolation and a time budget; returns (places, status)."""
    try:
        places = await asyncio.wait_for(
            source.search(lat, lng, radius_km), timeout=settings.discovery_source_timeout_seconds
        )
    except TimeoutError:
        logger.warning("Source %s timed out after %.0fs", name, settings.discovery_source_timeout_seconds)
        return [], STATUS_TIMEOUT
    except PartialSourceResult as partial:
        logger.warning("Source %s answered only in part: %s", name, partial.reason)
        return partial.places, STATUS_PARTIAL
    except (SourceError, httpx.HTTPError) as exc:
        # Outages, blocks and rate limits are routine: one line, not a traceback per request.
        logger.warning("Source %s unavailable: %s", name, exc or type(exc).__name__)
        return [], STATUS_ERROR
    except Exception:
        logger.exception("Source %s failed unexpectedly", name)
        return [], STATUS_ERROR
    return places, (STATUS_OK if places else STATUS_EMPTY)


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
    if sort_by == "unusual":
        # Most unusual first, but parts of one site (a fort's ruined buildings, a chain of
        # pillboxes, segments of one abandoned railway) share a slot instead of crowding the top.
        return diversify_by_site(sorted(places, key=unusual_sort_key))
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
