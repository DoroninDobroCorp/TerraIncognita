"""Collections: "3/12 water towers" style progress per category around a point.

One ``discover()`` call supplies the places; the journal's VISITED visits (read through its
public API) say which of them the explorer has been to. A place counts once for every
category it belongs to, so a "military + underground" bunker advances both collections.

Matching a visit to a discovered place:
1. by place id (exact);
2. otherwise by proximity (<= ``journal_proximity_radius_m``) *and* a compatible name, and
   each such visit claims at most one place (the nearest), so a nameless visit in a dense
   old town cannot tick off every neighbour. The journal does not store a category on a
   visit, so category cannot take part in the match.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict

from app.api.map_config import _CATEGORY_STYLES
from app.config import settings
from app.models.journal import Visit, VisitStatus
from app.models.place import DiscoverRequest, Place
from app.models.place_collections import Collection
from app.services import journal
from app.services.discovery import discover
from app.utils.geo import haversine_distance_m

logger = logging.getLogger(__name__)

_DISCOVER_LIMIT = 200  # DiscoverRequest.limit maximum: one call, best-ranked places first
_ALL_VISITS = 10**9
_METERS_PER_DEG_LAT = 111_320.0
_DEFAULT_ICON = "📍"

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _name_words(name: str) -> list[str]:
    return [t for t in _NON_WORD.split(name.casefold()) if t]


def names_similar(a: str | None, b: str | None) -> bool:
    """Whether two place names plausibly denote the same place.

    Equal, one contained in the other as whole words (when it has 4+ letters, so "Fort" can
    match "Fort Mamula" but "art" cannot match "party"), or sharing at least half of their
    words. A missing name is compatible with anything: the caller falls back to proximity.
    """
    wa = _name_words(a) if a else []
    wb = _name_words(b) if b else []
    if not wa or not wb:
        return True
    sa, sb = " ".join(wa), " ".join(wb)
    if sa == sb:
        return True
    shorter, longer = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(shorter) >= 4 and f" {shorter} " in f" {longer} ":
        return True
    return len(set(wa) & set(wb)) / len(set(wa) | set(wb)) >= 0.5


def find_visited_place_ids(
    places: list[Place], visits: list[Visit], proximity_m: float
) -> set[str]:
    """Ids of ``places`` the explorer has visited (see module docstring for the rules)."""
    # A merged place answers to every id that was folded into it (they depend on which
    # sources responded when the visit was recorded).
    by_id: dict[str, Place] = {}
    for p in places:
        by_id[p.id] = p
        for merged_id in p.metadata.get("merged_ids", ()):
            by_id.setdefault(merged_id, p)
    visited: set[str] = {by_id[v.place_id].id for v in visits if v.place_id in by_id}

    # Places not yet matched, sorted by latitude so a visit only looks at a thin strip.
    pending = sorted(
        (p for p in places if p.id not in visited), key=lambda p: p.coordinates.lat
    )
    lats = [p.coordinates.lat for p in pending]
    lat_window = proximity_m / _METERS_PER_DEG_LAT * 1.01
    claimed: set[str] = set()

    for v in visits:
        if v.place_id in by_id:
            continue
        lo = bisect_left(lats, v.coordinates.lat - lat_window)
        hi = bisect_right(lats, v.coordinates.lat + lat_window)
        best: tuple[float, Place] | None = None
        for p in pending[lo:hi]:
            if p.id in claimed or not names_similar(v.place_name, p.name):
                continue
            d = haversine_distance_m(
                v.coordinates.lat, v.coordinates.lng, p.coordinates.lat, p.coordinates.lng
            )
            if d <= proximity_m and (best is None or d < best[0]):
                best = (d, p)
        if best is not None:
            claimed.add(best[1].id)
    return visited | claimed


def summarize_collections(places: list[Place], visited_ids: set[str]) -> list[Collection]:
    """Per-category visited/total counts, biggest collections first, empty ones omitted."""
    total: dict[str, int] = defaultdict(int)
    done: dict[str, int] = defaultdict(int)
    for p in places:
        for cat in {c.value for c in p.categories}:
            total[cat] += 1
            if p.id in visited_ids:
                done[cat] += 1

    collections = []
    for cat, count in total.items():
        style = _CATEGORY_STYLES.get(cat)
        collections.append(Collection(
            category=cat,
            label=style.label if style else cat.replace("_", " ").title(),
            icon=style.icon if style else _DEFAULT_ICON,
            visited=done[cat],
            total=count,
        ))
    collections.sort(key=lambda c: (-c.total, c.category))
    return collections


async def build_collections(lat: float, lng: float, radius_km: float) -> list[Collection]:
    result = await discover(DiscoverRequest(
        lat=lat, lng=lng, radius_km=radius_km, limit=_DISCOVER_LIMIT, sort_by="confidence",
    ))
    if result.has_more:
        logger.info(
            "Collections truncated: %d of %d places considered", len(result.places), result.total,
        )
    visits, _ = await journal.list_visits(status=VisitStatus.VISITED, limit=_ALL_VISITS)
    visited = find_visited_place_ids(
        result.places, list(visits), settings.journal_proximity_radius_m
    )
    return summarize_collections(result.places, visited)
