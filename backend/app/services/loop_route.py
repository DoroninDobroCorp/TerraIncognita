"""Loop ("round trip in N hours") route planner.

Given a start point and a time budget, pick 2..max_stops interesting places and order them
into a closed tour that starts and ends at the start point and fits the budget:

    travel_time + stops * visit_time  <=  duration * (1 - loop_safety_margin)

with travel time from the *settings* speeds (``route_walking_speed_kmh`` /
``route_cycling_speed_kmh``) and ``route_visit_time_minutes`` per stop. Distances are
straight lines, like every other route in this app; the safety margin (default 10%) is what
absorbs real-path detours and breaks.

Selection, in order:
1. Discover around the start (radius derived from the budget; widened once if the area is
   sparse) and drop places a round trip cannot reach at all.
2. Spread: split the compass around the start into ``max_stops`` sectors and take the best
   place of each sector (quality discounted by distance), so the loop goes *around* instead
   of zig-zagging through one corner.
3. Order the picks with the existing NN + 2-opt helper (closing leg included) and compare
   with an angular sweep, keeping the shorter tour.
4. Trim: while over budget, drop the stop whose removal saves the most travel per unit of
   quality. If even two stops do not fit, fall back to the best affordable pair.
5. Fill: if time is left and there are fewer than max_stops, add more places by cheapest
   insertion (preferring unrepresented sectors), then polish the order once more. Polishing
   never makes a tour longer, so the budget guarantee holds throughout.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

from app.config import settings
from app.models.loop_route import LoopRouteRequest
from app.models.place import Coordinates, DiscoverRequest, Place
from app.models.route import Route, RouteResponse, TransportMode
from app.services.discovery import discover
from app.services.route_builder import (
    _build_route_object,
    _format_summary,
    _generate_nav_links,
)
from app.services.route_optimizer import distance_m, optimize_route

logger = logging.getLogger(__name__)

_DISCOVER_LIMIT = 200
_MIN_STOP_SEPARATION_M = 75.0  # near-duplicate places are not two stops
_PAIR_POOL = 30  # candidates considered by the "best affordable pair" fallback
_MIN_DISCOVER_KM = 0.5


class LoopParamsError(ValueError):
    """The request is valid JSON but no loop can fit (e.g. too short for two visits)."""


class NotEnoughPlacesError(RuntimeError):
    """Too few reachable places around the start to build a loop."""


@dataclass(frozen=True)
class LoopBudget:
    """Time arithmetic of one loop request."""

    speed_ms: float
    visit_s: float
    usable_s: float
    margin: float = 0.0  # share of the requested duration held back (already out of usable_s)

    @classmethod
    def for_request(cls, duration_hours: float, mode: str) -> LoopBudget:
        speed_kmh = (
            settings.route_cycling_speed_kmh if mode == "cycling"
            else settings.route_walking_speed_kmh
        )
        margin = min(max(settings.loop_safety_margin, 0.0), 0.9)  # tolerate silly settings
        return cls(
            speed_ms=max(speed_kmh, 0.5) / 3.6,
            visit_s=max(settings.route_visit_time_minutes, 0.0) * 60.0,
            usable_s=duration_hours * 3600.0 * (1.0 - margin),
            margin=margin,
        )

    def time_s(self, length_m: float, stops: int) -> float:
        return length_m / self.speed_ms + stops * self.visit_s

    def fits(self, length_m: float, stops: int) -> bool:
        return self.time_s(length_m, stops) <= self.usable_s

    @property
    def reach_m(self) -> float:
        """Farthest a single stop can be and still allow a there-and-back trip."""
        return max((self.usable_s - self.visit_s) * self.speed_ms / 2.0, 0.0)


# ── Geometry and ordering ────────────────────────────────────────


def loop_length_m(origin: Coordinates, stops: list[Place]) -> float:
    """Length of origin -> stops in order -> origin (the closing leg included)."""
    if not stops:
        return 0.0
    pts = [origin, *(s.coordinates for s in stops), origin]
    return sum(distance_m(a, b) for a, b in zip(pts, pts[1:], strict=False))


def bearing_deg(origin: Coordinates, target: Coordinates) -> float:
    """Compass bearing 0..360 (north = 0, clockwise) with a local flat-earth projection."""
    d_lat = target.lat - origin.lat
    d_lng = (target.lng - origin.lng) * math.cos(math.radians(origin.lat))
    return math.degrees(math.atan2(d_lng, d_lat)) % 360.0


def angular_order(origin: Coordinates, stops: list[Place]) -> list[Place]:
    """Visit stops in order of bearing around the start, cut where the tour is shortest."""
    if len(stops) <= 2:
        return list(stops)
    by_bearing = sorted(stops, key=lambda p: (bearing_deg(origin, p.coordinates), p.id))
    rotations = [by_bearing[i:] + by_bearing[:i] for i in range(len(by_bearing))]
    return min(rotations, key=lambda order: loop_length_m(origin, order))


def best_order(
    origin: Coordinates, stops: list[Place], seeds: list[list[Place]] | None = None
) -> list[Place]:
    """Shortest of: NN + 2-opt (route_optimizer), angular sweep, and any ``seeds``.

    Including the caller's current tour as a seed means this never returns a longer one.
    """
    if len(stops) <= 1:
        return list(stops)
    candidates = [
        optimize_route(origin, list(stops), is_circular=True),
        angular_order(origin, stops),
        *(seeds or []),
    ]
    return min(candidates, key=lambda order: loop_length_m(origin, order))


def stop_score(place: Place) -> float:
    """How much we want this place on a loop (quality only; distance is handled apart)."""
    unusual_score = getattr(place, "unusual_score", 0.0)
    unusual_score = min(max(unusual_score, 0.0), 1.0) if isinstance(unusual_score, int | float) else 0.0
    return (
        place.confidence
        + (0.5 if place.is_unusual else 0.0)
        + 0.5 * unusual_score
        + (0.25 if place.name else 0.0)  # a stop called "Point 4" in the GPX is a poor stop
    )


@dataclass(frozen=True)
class _Candidate:
    place: Place
    dist_m: float
    sector: int
    utility: float

    @property
    def order_key(self) -> tuple[float, str]:
        return (-self.utility, self.place.id)


def _candidates(
    origin: Coordinates, places: list[Place], budget: LoopBudget, sectors: int
) -> list[_Candidate]:
    """Reachable, de-duplicated places with their sector and distance-discounted quality."""
    reach = budget.reach_m
    width = 360.0 / sectors
    seen: set[str] = set()
    out = []
    for p in places:
        if p.id in seen:
            continue
        seen.add(p.id)
        d = distance_m(origin, p.coordinates)
        if d > reach:
            continue
        sector = int(bearing_deg(origin, p.coordinates) // width) % sectors
        utility = stop_score(p) * (1.0 - 0.5 * min(d / reach, 1.0)) if reach > 0 else 0.0
        out.append(_Candidate(p, d, sector, utility))
    return out


def _too_close(c: _Candidate, chosen: list[Place]) -> bool:
    return any(distance_m(c.place.coordinates, s.coordinates) < _MIN_STOP_SEPARATION_M for s in chosen)


def _drop_least_worthwhile(
    origin: Coordinates, order: list[Place], utility: dict[str, float]
) -> list[Place]:
    """Remove the stop that costs the most extra travel per unit of quality."""
    def detour(i: int) -> float:
        prev = origin if i == 0 else order[i - 1].coordinates
        nxt = origin if i == len(order) - 1 else order[i + 1].coordinates
        here = order[i].coordinates
        return distance_m(prev, here) + distance_m(here, nxt) - distance_m(prev, nxt)

    worst = max(
        range(len(order)),
        key=lambda i: (detour(i) / max(utility[order[i].id], 1e-6), order[i].id),
    )
    return order[:worst] + order[worst + 1 :]


def _best_pair(
    origin: Coordinates, cands: list[_Candidate], budget: LoopBudget
) -> list[Place] | None:
    """Highest-quality two stops whose loop fits, or None."""
    top = sorted(cands, key=lambda c: c.order_key)[:_PAIR_POOL]
    best: tuple[float, list[str], list[Place]] | None = None
    for i, a in enumerate(top):
        for b in top[i + 1 :]:
            if _too_close(b, [a.place]):
                continue
            order = [a.place, b.place]
            if not budget.fits(loop_length_m(origin, order), 2):
                continue
            key = (a.utility + b.utility, sorted([a.place.id, b.place.id]))
            if best is None or key[0] > best[0] or (key[0] == best[0] and key[1] < best[1]):
                best = (key[0], key[1], order)
    return best[2] if best else None


def _cheapest_insertion(
    origin: Coordinates, order: list[Place], place: Place
) -> tuple[int, float]:
    """(position, extra metres) of inserting ``place`` into the closed tour at least cost."""
    pts = [origin, *(s.coordinates for s in order), origin]
    best_pos, best_extra = 0, math.inf
    for i in range(len(pts) - 1):
        extra = (
            distance_m(pts[i], place.coordinates)
            + distance_m(place.coordinates, pts[i + 1])
            - distance_m(pts[i], pts[i + 1])
        )
        if extra < best_extra:
            best_pos, best_extra = i, extra
    return best_pos, best_extra


def select_stops(
    origin: Coordinates, places: list[Place], budget: LoopBudget, max_stops: int
) -> list[Place] | None:
    """Ordered stops (2..max_stops) forming a loop that fits ``budget``, or None."""
    cands = _candidates(origin, places, budget, sectors=max_stops)
    if len(cands) < 2:
        return None
    by_id = {c.place.id: c for c in cands}
    utility = {c.place.id: c.utility for c in cands}

    # 2. spread: best candidate of every sector
    per_sector: dict[int, _Candidate] = {}
    for c in sorted(cands, key=lambda c: c.order_key):
        per_sector.setdefault(c.sector, c)
    picks: list[Place] = []
    for c in sorted(per_sector.values(), key=lambda c: c.order_key):
        if not _too_close(c, picks):
            picks.append(c.place)

    # 3 + 4. order, then trim until the loop fits
    order = best_order(origin, picks)
    while len(order) > 2 and not budget.fits(loop_length_m(origin, order), len(order)):
        order = _drop_least_worthwhile(origin, order, utility)
    if len(order) < 2 or not budget.fits(loop_length_m(origin, order), len(order)):
        pair = _best_pair(origin, cands, budget)
        if pair is None:
            return None
        order = pair

    # 5. fill the remaining time by cheapest insertion, new sectors first
    length = loop_length_m(origin, order)
    while len(order) < max_stops:
        used = {by_id[p.id].sector for p in order}
        chosen_ids = {p.id for p in order}
        spare = sorted(
            (c for c in cands if c.place.id not in chosen_ids),
            key=lambda c: (c.sector in used, *c.order_key),
        )
        for c in spare:
            if _too_close(c, order):
                continue
            pos, extra = _cheapest_insertion(origin, order, c.place)
            if budget.fits(length + extra, len(order) + 1):
                order = order[:pos] + [c.place] + order[pos:]
                length += extra
                break
        else:
            break  # nothing else fits

    polished = best_order(origin, order, seeds=[order])
    return polished


# ── Orchestration ────────────────────────────────────────────────


def _discovery_radii_km(budget: LoopBudget) -> list[float]:
    """Radii to try: ~speed*time/3 from the start, widened once to the farthest reachable."""
    cap = min(settings.max_radius_km, 50.0)
    travel_s = budget.usable_s - 2 * budget.visit_s  # time left for walking with 2 stops
    typical = min(max(budget.speed_ms * travel_s / 3.0 / 1000.0, _MIN_DISCOVER_KM), cap)
    farthest = min(max(budget.reach_m / 1000.0, typical), cap)
    return [typical] if farthest <= typical * 1.25 else [typical, farthest]


def _format_duration(seconds: float) -> str:
    minutes = int(round(seconds / 60.0))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}min" if hours else f"{minutes}min"


def _apply_budget_timing(route: Route, budget: LoopBudget) -> None:
    """Report travel times with the same speed the budget used (not the optimizer default)."""
    total = 0.0
    for seg in route.segments:
        seg.duration_s = round(seg.distance_m / budget.speed_ms, 1)
        total += seg.duration_s
    route.total_duration_s = round(total, 1)
    for wp in route.waypoints:
        if not wp.is_origin and not wp.is_destination:
            wp.detour_duration_s = round(wp.detour_distance_m / budget.speed_ms, 1)


async def build_loop_route(req: LoopRouteRequest) -> RouteResponse:
    t0 = time.monotonic()
    budget = LoopBudget.for_request(req.duration_hours, req.mode)
    min_time_s = 2 * budget.visit_s
    if budget.usable_s <= min_time_s:
        needed_min = math.ceil(min_time_s / (1.0 - budget.margin) / 60.0) + 5
        raise LoopParamsError(
            f"{req.duration_hours:g} h is too short for two stops at "
            f"{settings.route_visit_time_minutes:g} min per visit; "
            f"ask for at least {needed_min} minutes."
        )

    origin = Coordinates(lat=req.lat, lng=req.lng)
    stops: list[Place] | None = None
    discovered = 0
    searched_km = 0.0
    for radius_km in _discovery_radii_km(budget):
        searched_km = radius_km
        result = await discover(DiscoverRequest(
            lat=req.lat, lng=req.lng, radius_km=radius_km, categories=req.categories,
            exclude_visited=req.exclude_visited, limit=_DISCOVER_LIMIT, sort_by="confidence",
        ))
        discovered = len(result.places)
        if len(_candidates(origin, result.places, budget, req.max_stops)) < 2:
            continue  # sparse area: widen once
        stops = select_stops(origin, result.places, budget, req.max_stops)
        break
    if stops is None:
        raise NotEnoughPlacesError(
            f"Not enough interesting places within reach for a {req.duration_hours:g}-hour "
            f"{req.mode} loop (searched {searched_km:.1f} km around the start). Try more "
            "time, a faster mode, or different categories."
        )

    route = _build_route_object(
        origin=origin, destination=None, places=stops,
        transport_mode=TransportMode(req.mode), is_circular=True,
    )
    _apply_budget_timing(route, budget)
    visit_total_s = len(stops) * budget.visit_s
    route.metadata.update({
        "kind": "loop",
        "duration_budget_s": round(req.duration_hours * 3600.0, 1),
        "usable_budget_s": round(budget.usable_s, 1),
        "travel_time_s": route.total_duration_s,
        "visit_time_s": round(visit_total_s, 1),
        "total_time_s": round(route.total_duration_s + visit_total_s, 1),
        "visit_minutes_per_stop": settings.route_visit_time_minutes,
    })
    summary = (
        f"{_format_summary(route)}; about "
        f"{_format_duration(route.total_duration_s + visit_total_s)} including visits"
    )
    logger.info(
        "Loop built: %d stops, %.0fm, %.0f of %.0f usable s in %.2fs (discovered: %d)",
        len(stops), route.total_distance_m, route.total_duration_s + visit_total_s,
        budget.usable_s, time.monotonic() - t0, discovered,
    )
    return RouteResponse(
        route=route, discovered_places=discovered, summary=summary,
        navigation_links=_generate_nav_links(route),
    )
