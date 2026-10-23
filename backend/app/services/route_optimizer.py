"""Route optimization algorithms (TSP solver with 2-opt improvement).

Implements nearest-neighbor heuristic + 2-opt local search for
efficient route ordering through N waypoints.
"""

from __future__ import annotations

import logging
import math
import time

from app.models.place import Coordinates, Place
from app.utils.geo import haversine_distance_m

logger = logging.getLogger(__name__)

# Average speeds in m/s for duration estimation
SPEED_MS: dict[str, float] = {
    "walking": 1.4,   # ~5 km/h
    "cycling": 4.2,   # ~15 km/h
    "driving": 11.1,  # ~40 km/h (urban average)
}


def distance_m(a: Coordinates, b: Coordinates) -> float:
    """Haversine distance in meters between two coordinates."""
    return haversine_distance_m(a.lat, a.lng, b.lat, b.lng)


def _total_routedistance_m(coords: list[Coordinates]) -> float:
    """Total distance traversing coordinates in order."""
    return sum(distance_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def nearest_neighbor_order(
    origin: Coordinates,
    waypoints: list[Coordinates],
    destination: Coordinates | None = None,
) -> list[int]:
    """Order waypoints using the nearest-neighbor heuristic.

    Returns indices into the `waypoints` list.
    """
    if not waypoints:
        return []

    n = len(waypoints)
    visited = [False] * n
    order: list[int] = []
    current = origin

    for _ in range(n):
        best_idx = -1
        best_dist = math.inf
        for j in range(n):
            if visited[j]:
                continue
            d = distance_m(current, waypoints[j])
            if d < best_dist:
                best_dist = d
                best_idx = j
        visited[best_idx] = True
        order.append(best_idx)
        current = waypoints[best_idx]

    return order


# 2-opt works on a full distance matrix: beyond this many stops only the nearest-neighbour
# order is used (discovery never returns more than 200, so this is a safety net).
MAX_2OPT_POINTS = 400


def two_opt_improve(
    origin: Coordinates,
    waypoints: list[Coordinates],
    destination: Coordinates | None = None,
    max_iterations: int = 100,
    time_limit_s: float = 2.0,
) -> list[int]:
    """Improve waypoint order using 2-opt local search.

    Returns optimized indices into the `waypoints` list. The tour runs origin -> waypoints ->
    ``destination`` (or back to ``origin`` when there is none).

    Each candidate reversal is judged by the change in the four edges it touches
    (O(1) on a precomputed distance matrix) instead of re-summing the whole tour (O(n)):
    120 stops took ~3 s and 200 stops ~15 s, blocking the event loop of the async handler.
    The search also stops after ``time_limit_s`` and returns the best order found so far.
    """
    n = len(waypoints)
    if n <= 1:
        return list(range(n))

    # Start with nearest-neighbor solution
    order = nearest_neighbor_order(origin, waypoints, destination)
    if n > MAX_2OPT_POINTS:
        return order

    # Node 0 = origin, 1..n = waypoints, n + 1 = end of the tour
    nodes = [origin, *waypoints, destination or origin]
    dist = [[0.0] * (n + 2) for _ in range(n + 2)]
    for a in range(n + 2):
        for b in range(a + 1, n + 2):
            dist[a][b] = dist[b][a] = distance_m(nodes[a], nodes[b])

    tour = [0] + [i + 1 for i in order] + [n + 1]  # positions 1..n are the movable stops
    deadline = time.monotonic() + time_limit_s
    improved = True
    iterations = 0

    while improved and iterations < max_iterations and time.monotonic() < deadline:
        improved = False
        iterations += 1
        for i in range(1, n):
            for j in range(i + 1, n + 1):
                prev_node, next_node = tour[i - 1], tour[j + 1]
                delta = (
                    dist[prev_node][tour[j]] + dist[tour[i]][next_node]
                    - dist[prev_node][tour[i]] - dist[tour[j]][next_node]
                )
                if delta < -1e-9:
                    tour[i : j + 1] = tour[i : j + 1][::-1]
                    improved = True

    result = [node - 1 for node in tour[1 : n + 1]]
    logger.info(
        "2-opt optimization: %d points, %d iterations, final distance: %.0fm",
        n, iterations, sum(dist[tour[k]][tour[k + 1]] for k in range(n + 1)),
    )
    return result


def optimize_route(
    origin: Coordinates,
    places: list[Place],
    destination: Coordinates | None = None,
    is_circular: bool = False,
) -> list[Place]:
    """Optimize the visit order for a list of places.

    Uses nearest-neighbor + 2-opt for TSP-like optimization.
    Returns places in optimized order.
    """
    if not places:
        return []

    if len(places) == 1:
        return list(places)

    waypoint_coords = [p.coordinates for p in places]
    end = None if is_circular else destination

    order = two_opt_improve(origin, waypoint_coords, destination=end)
    return [places[i] for i in order]


def estimate_duration_s(distance_m: float, transport_mode: str) -> float:
    """Estimate travel duration in seconds for given distance and mode."""
    speed = SPEED_MS.get(transport_mode, SPEED_MS["walking"])
    return distance_m / speed


def filter_by_time_budget(
    origin: Coordinates,
    places: list[Place],
    max_duration_s: float,
    transport_mode: str,
    is_circular: bool = False,
    visit_time_s: float = 600.0,
    destination: Coordinates | None = None,
) -> list[Place]:
    """Filter places to fit within a time budget.

    Greedily adds places from the optimized order until the time
    budget is exhausted. Each place adds its travel time + a fixed
    visit duration. For a circular route the way back to ``origin`` is reserved; for a
    point-to-point route (``destination`` given) the way on to the destination is — it used
    to be ignored, so the finished route could take hours longer than requested.
    """
    if not places:
        return []

    result: list[Place] = []
    current = origin
    elapsed = 0.0

    for place in places:
        travel_d = distance_m(current, place.coordinates)
        travel_t = estimate_duration_s(travel_d, transport_mode)

        # Check if we can still return home if circular
        if is_circular:
            return_d = distance_m(place.coordinates, origin)
            return_t = estimate_duration_s(return_d, transport_mode)
            total_if_added = elapsed + travel_t + visit_time_s + return_t
        elif destination is not None:
            onward_d = distance_m(place.coordinates, destination)
            total_if_added = (
                elapsed + travel_t + visit_time_s + estimate_duration_s(onward_d, transport_mode)
            )
        else:
            total_if_added = elapsed + travel_t + visit_time_s

        if total_if_added > max_duration_s:
            continue

        result.append(place)
        elapsed += travel_t + visit_time_s
        current = place.coordinates

    return result


def find_places_in_corridor(
    places: list[Place],
    origin: Coordinates,
    destination: Coordinates,
    corridor_width_km: float,
) -> list[Place]:
    """Find places within a corridor between origin and destination.

    The corridor is defined as all points within `corridor_width_km`
    of the straight line between origin and destination.
    """
    corridor_m = corridor_width_km * 1000
    result: list[Place] = []

    # Line segment: origin -> destination
    # For each place, compute distance to this line segment
    for place in places:
        dist = point_to_segment_distance(
            place.coordinates, origin, destination
        )
        if dist <= corridor_m:
            result.append(place)

    return result


def point_to_segment_distance(
    point: Coordinates, seg_start: Coordinates, seg_end: Coordinates
) -> float:
    """Approximate distance from a point to a line segment on Earth surface.

    Uses planar approximation with latitude correction (sufficient
    for corridor widths up to ~10km).
    """
    # Convert to approximate planar coordinates (meters)
    avg_lat = math.radians((seg_start.lat + seg_end.lat) / 2)
    cos_lat = math.cos(avg_lat)
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * cos_lat

    px = (point.lng - seg_start.lng) * m_per_deg_lng
    py = (point.lat - seg_start.lat) * m_per_deg_lat
    ax = 0.0
    ay = 0.0
    bx = (seg_end.lng - seg_start.lng) * m_per_deg_lng
    by = (seg_end.lat - seg_start.lat) * m_per_deg_lat

    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq == 0:
        return math.sqrt(px * px + py * py)

    t = max(0.0, min(1.0, (px * dx + py * dy) / seg_len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy

    return math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)
