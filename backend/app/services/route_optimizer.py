"""Route optimization algorithms (TSP solver with 2-opt improvement).

Implements nearest-neighbor heuristic + 2-opt local search for
efficient route ordering through N waypoints.
"""

from __future__ import annotations

import logging
import math

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


def two_opt_improve(
    origin: Coordinates,
    waypoints: list[Coordinates],
    destination: Coordinates | None = None,
    max_iterations: int = 100,
) -> list[int]:
    """Improve waypoint order using 2-opt local search.

    Returns optimized indices into the `waypoints` list.
    """
    if len(waypoints) <= 2:
        return list(range(len(waypoints)))

    # Start with nearest-neighbor solution
    order = nearest_neighbor_order(origin, waypoints, destination)

    end_point = destination or origin

    def route_cost(idx_order: list[int]) -> float:
        total = distance_m(origin, waypoints[idx_order[0]])
        for k in range(len(idx_order) - 1):
            total += distance_m(waypoints[idx_order[k]], waypoints[idx_order[k + 1]])
        total += distance_m(waypoints[idx_order[-1]], end_point)
        return total

    best_cost = route_cost(order)
    improved = True
    iterations = 0

    while improved and iterations < max_iterations:
        improved = False
        iterations += 1
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                new_order = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
                new_cost = route_cost(new_order)
                if new_cost < best_cost:
                    order = new_order
                    best_cost = new_cost
                    improved = True

    logger.info(
        "2-opt optimization: %d iterations, final distance: %.0fm",
        iterations, best_cost,
    )
    return order


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
        return places

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
) -> list[Place]:
    """Filter places to fit within a time budget.

    Greedily adds places from the optimized order until the time
    budget is exhausted. Each place adds its travel time + a fixed
    visit duration.
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
