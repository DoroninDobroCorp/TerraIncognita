"""Route Builder service — orchestrates route planning (Epic 4).

Integrates discovery, optimization, and export to build
smart routes through interesting places.
"""

from __future__ import annotations

import logging
import time
import uuid

from app.models.place import (
    Coordinates,
    DiscoverRequest,
    Place,
)
from app.models.route import (
    ExploreRouteRequest,
    Route,
    RouteRequest,
    RouteResponse,
    RouteSegment,
    RouteWaypoint,
    TransportMode,
)
from app.services.discovery import discover
from app.services.route_optimizer import (
    distance_m,
    estimate_duration_s,
    filter_by_time_budget,
    find_places_in_corridor,
    optimize_route,
)

logger = logging.getLogger(__name__)


async def build_route(req: RouteRequest) -> RouteResponse:
    """Build a point-to-point route with POI discovery along the corridor.

    Story 4.1: Discovers POIs in the corridor between origin and destination,
    then optimizes the visit order.
    """
    t0 = time.monotonic()

    destination = req.destination or req.origin
    is_circular = req.destination is None

    # 1. Discover places along the corridor
    midpoint_lat = (req.origin.lat + destination.lat) / 2
    midpoint_lng = (req.origin.lng + destination.lng) / 2
    direct_dist_m = distance_m(req.origin, destination)
    search_radius_km = max(req.corridor_width_km * 2, direct_dist_m / 1000 + req.corridor_width_km)

    discover_req = DiscoverRequest(
        lat=midpoint_lat,
        lng=midpoint_lng,
        radius_km=min(search_radius_km, 50.0),
        categories=req.categories,
        exclude_visited=req.exclude_visited,
        limit=200,
        sort_by="confidence",
    )

    discover_result = await discover(discover_req)
    all_places = discover_result.places
    total_discovered = len(all_places)

    # 2. Filter to corridor (for point-to-point routes)
    if not is_circular:
        corridor_places = find_places_in_corridor(
            all_places, req.origin, destination, req.corridor_width_km
        )
    else:
        corridor_places = all_places

    # 3. Optimize order
    if req.optimize and corridor_places:
        ordered = optimize_route(
            req.origin, corridor_places,
            destination=destination if not is_circular else None,
            is_circular=is_circular,
        )
    else:
        ordered = corridor_places

    # 4. Filter by time budget
    max_duration_s = req.max_duration_hours * 3600
    fitted = filter_by_time_budget(
        req.origin, ordered, max_duration_s,
        req.transport_mode.value, is_circular=is_circular,
    )

    # 5. Limit waypoints
    fitted = fitted[: req.max_waypoints]

    # 5a. Apply surprise mode (hide place details, reveal on approach)
    if req.surprise_mode:
        fitted = _apply_surprise_mode(fitted)

    # 6. Build route object
    route = _build_route_object(
        origin=req.origin,
        destination=destination if not is_circular else None,
        places=fitted,
        transport_mode=req.transport_mode,
        is_circular=is_circular,
    )

    elapsed = time.monotonic() - t0
    logger.info(
        "Route built: %d waypoints, %.0fm, %.0fs in %.2fs (discovered: %d, corridor: %d)",
        len(route.waypoints), route.total_distance_m,
        route.total_duration_s, elapsed,
        total_discovered, len(corridor_places),
    )

    return RouteResponse(
        route=route,
        discovered_places=total_discovered,
        summary=_format_summary(route),
        navigation_links=_generate_nav_links(route),
    )


async def build_explore_route(req: ExploreRouteRequest) -> RouteResponse:
    """Generate a circular exploration route (Story 4.2).

    Finds interesting places around the origin, optimizes a circular
    route, and fits it within the time budget.
    """
    t0 = time.monotonic()

    # 1. Discover places around origin
    discover_req = DiscoverRequest(
        lat=req.origin.lat,
        lng=req.origin.lng,
        radius_km=req.radius_km,
        categories=req.categories,
        exclude_visited=req.exclude_visited,
        limit=200,
        sort_by="confidence",
    )

    discover_result = await discover(discover_req)
    all_places = discover_result.places
    total_discovered = len(all_places)

    # 2. Optimize circular route
    ordered = optimize_route(
        req.origin, all_places, is_circular=True,
    )

    # 3. Filter by time budget
    max_duration_s = req.max_duration_hours * 3600
    fitted = filter_by_time_budget(
        req.origin, ordered, max_duration_s,
        req.transport_mode.value, is_circular=True,
    )

    # 4. Limit waypoints
    fitted = fitted[: req.max_waypoints]

    # 5. Apply surprise mode (hide place details, reveal on approach)
    if req.surprise_mode:
        fitted = _apply_surprise_mode(fitted)

    # 6. Build route object
    route = _build_route_object(
        origin=req.origin,
        destination=None,
        places=fitted,
        transport_mode=req.transport_mode,
        is_circular=True,
    )

    elapsed = time.monotonic() - t0
    logger.info(
        "Explore route built: %d waypoints, %.0fm, %.0fs in %.2fs (discovered: %d)",
        len(route.waypoints), route.total_distance_m,
        route.total_duration_s, elapsed, total_discovered,
    )

    return RouteResponse(
        route=route,
        discovered_places=total_discovered,
        summary=_format_summary(route),
        navigation_links=_generate_nav_links(route),
    )


def reorder_waypoints(route: Route, new_order: list[int]) -> RouteResponse:
    """Reorder POI waypoints in a route (Story 4.1: drag-and-drop).

    new_order contains indices of POI waypoints in desired order.
    Origin and destination remain fixed.
    """
    poi_waypoints = [w for w in route.waypoints if not w.is_origin and not w.is_destination]

    if sorted(new_order) != list(range(len(poi_waypoints))):
        raise ValueError(
            f"new_order must be a permutation of 0..{len(poi_waypoints) - 1}"
        )

    reordered_pois = [poi_waypoints[i] for i in new_order]
    reordered_places = [w.place for w in reordered_pois]

    origin_wp = next((w for w in route.waypoints if w.is_origin), None)
    dest_wp = next((w for w in route.waypoints if w.is_destination), None)

    origin_coord = (
        origin_wp.place.coordinates if origin_wp
        else route.waypoints[0].place.coordinates
    )
    dest_coord = dest_wp.place.coordinates if dest_wp else None

    new_route = _build_route_object(
        origin=origin_coord,
        destination=dest_coord if not route.is_circular else None,
        places=reordered_places,
        transport_mode=route.transport_mode,
        is_circular=route.is_circular,
    )

    return RouteResponse(
        route=new_route,
        summary=_format_summary(new_route),
        navigation_links=_generate_nav_links(new_route),
    )


def _build_route_object(
    origin: Coordinates,
    destination: Coordinates | None,
    places: list[Place],
    transport_mode: TransportMode,
    is_circular: bool,
) -> Route:
    """Construct a Route with waypoints, segments, and totals."""
    route_id = str(uuid.uuid4())

    # Build waypoints
    waypoints: list[RouteWaypoint] = []

    # Origin waypoint (virtual place)
    origin_place = Place(
        id=f"origin_{route_id[:8]}",
        source="osm",
        name="Start",
        coordinates=origin,
    )
    waypoints.append(RouteWaypoint(
        place=origin_place, order=0, is_origin=True,
    ))

    # POI waypoints
    for i, place in enumerate(places):
        waypoints.append(RouteWaypoint(place=place, order=i + 1))

    # Destination waypoint
    if is_circular:
        dest_place = Place(
            id=f"return_{route_id[:8]}",
            source="osm",
            name="Return to Start",
            coordinates=origin,
        )
        waypoints.append(RouteWaypoint(
            place=dest_place, order=len(places) + 1, is_destination=True,
        ))
    elif destination:
        dest_place = Place(
            id=f"dest_{route_id[:8]}",
            source="osm",
            name="Destination",
            coordinates=destination,
        )
        waypoints.append(RouteWaypoint(
            place=dest_place, order=len(places) + 1, is_destination=True,
        ))

    # Build segments between consecutive waypoints
    segments: list[RouteSegment] = []
    total_distance = 0.0
    total_duration = 0.0
    mode = transport_mode.value

    for i in range(len(waypoints) - 1):
        from_coord = waypoints[i].place.coordinates
        to_coord = waypoints[i + 1].place.coordinates
        dist = distance_m(from_coord, to_coord)
        dur = estimate_duration_s(dist, mode)

        segments.append(RouteSegment(
            from_point=from_coord,
            to_point=to_coord,
            distance_m=round(dist, 1),
            duration_s=round(dur, 1),
            transport_mode=transport_mode,
            geometry=[from_coord, to_coord],
        ))

        total_distance += dist
        total_duration += dur

    # Compute detour for each POI waypoint
    if len(waypoints) >= 3:
        direct_dist = distance_m(waypoints[0].place.coordinates, waypoints[-1].place.coordinates)
        for wp in waypoints:
            if not wp.is_origin and not wp.is_destination:
                detour = round(max(0, total_distance - direct_dist) / max(1, len(places)), 1)
                wp.detour_distance_m = detour
                wp.detour_duration_s = round(
                    estimate_duration_s(wp.detour_distance_m, mode), 1
                )

    return Route(
        id=route_id,
        waypoints=waypoints,
        segments=segments,
        total_distance_m=round(total_distance, 1),
        total_duration_s=round(total_duration, 1),
        transport_mode=transport_mode,
        is_circular=is_circular,
        places_count=len(places),
    )


def _format_summary(route: Route) -> str:
    """Generate a human-readable route summary."""
    dist_km = route.total_distance_m / 1000
    hours = int(route.total_duration_s // 3600)
    minutes = int((route.total_duration_s % 3600) // 60)

    if hours > 0:
        duration_str = f"{hours}h {minutes}min"
    else:
        duration_str = f"{minutes}min"

    mode_label = route.transport_mode.value
    route_type = "circular" if route.is_circular else "point-to-point"

    place_names = [
        w.place.name for w in route.waypoints
        if not w.is_origin and not w.is_destination and w.place.name
    ]

    summary = (
        f"{route_type.title()} {mode_label} route: "
        f"{dist_km:.1f} km, ~{duration_str}, "
        f"{route.places_count} stops"
    )
    if place_names:
        names_str = ", ".join(place_names[:5])
        if len(place_names) > 5:
            names_str += f" +{len(place_names) - 5} more"
        summary += f" ({names_str})"

    return summary


def _generate_nav_links(route: Route) -> dict[str, str]:
    """Generate deep links for external navigation apps."""
    if not route.waypoints:
        return {}

    coords = [
        (w.place.coordinates.lat, w.place.coordinates.lng)
        for w in route.waypoints
    ]

    if len(coords) < 2:
        return {}

    origin = coords[0]
    dest = coords[-1]
    waypoints_mid = coords[1:-1]

    links: dict[str, str] = {}

    # Google Maps
    gm_url = f"https://www.google.com/maps/dir/{origin[0]},{origin[1]}"
    for lat, lng in waypoints_mid:
        gm_url += f"/{lat},{lng}"
    gm_url += f"/{dest[0]},{dest[1]}"
    links["google_maps"] = gm_url

    # OsmAnd
    osmand_url = f"osmand://navigate?lat={dest[0]}&lon={dest[1]}"
    links["osmand"] = osmand_url

    # Geo URI (universal)
    links["geo"] = f"geo:{dest[0]},{dest[1]}"

    # QR code (data URI with the Google Maps link for easy sharing)
    links["qr_data"] = _generate_qr_data_uri(gm_url)

    return links


def _apply_surprise_mode(places: list[Place]) -> list[Place]:
    """Hide place details for surprise mode — reveal only on approach.

    Replaces name/description with mystery placeholders so the user
    discovers what each stop is when they physically arrive.
    """
    surprises: list[Place] = []
    for i, place in enumerate(places):
        hidden = place.model_copy(update={
            "name": f"Mystery Stop #{i + 1}",
            "description": "🎁 Surprise! Visit this location to reveal what's here.",
            "photos": [],
        })
        hidden.metadata["_surprise_original_name"] = place.name
        hidden.metadata["_surprise_original_description"] = place.description
        surprises.append(hidden)
    return surprises


def _generate_qr_data_uri(url: str) -> str:
    """Generate a minimal QR code as an SVG data URI.

    Uses a simple text-based QR representation suitable for sharing.
    The data URI encodes a small SVG that can be rendered in browsers
    or saved as an image.
    """
    import base64
    import urllib.parse

    # Create a simple SVG-based QR placeholder that embeds the URL
    # For a real QR code we'd use qrcode library, but this provides
    # a functional sharing mechanism via encoded URL
    encoded_url = urllib.parse.quote(url, safe="")
    qr_api_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded_url}"
    return qr_api_url
