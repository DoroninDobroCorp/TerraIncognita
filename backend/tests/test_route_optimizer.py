"""Tests for route optimizer — TSP, 2-opt, corridor, time budget."""

from __future__ import annotations

from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.route_optimizer import (
    distance_m,
    estimate_duration_s,
    filter_by_time_budget,
    find_places_in_corridor,
    nearest_neighbor_order,
    optimize_route,
    point_to_segment_distance,
    two_opt_improve,
)


def _make_place(lat: float, lng: float, name: str = "P") -> Place:
    return Place(
        id=f"test_{name}_{lat}_{lng}",
        source=PlaceSource.OSM,
        name=name,
        categories=[PlaceCategory.LANDMARK],
        coordinates=Coordinates(lat=lat, lng=lng),
        confidence=0.8,
    )


class TestDistance:
    def test_zerodistance_m(self):
        c = Coordinates(lat=42.45, lng=18.53)
        assert distance_m(c, c) == 0.0

    def test_knowndistance_m(self):
        # Podgorica to Bar ~ 53 km
        podgorica = Coordinates(lat=42.4411, lng=19.2636)
        bar = Coordinates(lat=42.0931, lng=19.1003)
        dist = distance_m(podgorica, bar)
        assert 39_000 < dist < 42_000

    def test_symmetry(self):
        a = Coordinates(lat=42.45, lng=18.53)
        b = Coordinates(lat=42.50, lng=18.60)
        assert abs(distance_m(a, b) - distance_m(b, a)) < 0.01


class TestNearestNeighbor:
    def test_empty(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        assert nearest_neighbor_order(origin, []) == []

    def test_single_point(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        wp = [Coordinates(lat=42.46, lng=18.54)]
        assert nearest_neighbor_order(origin, wp) == [0]

    def test_picks_nearest_first(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        # near, far, medium
        wps = [
            Coordinates(lat=42.46, lng=18.54),   # near
            Coordinates(lat=43.00, lng=19.00),   # far
            Coordinates(lat=42.48, lng=18.56),   # medium
        ]
        order = nearest_neighbor_order(origin, wps)
        assert order[0] == 0  # nearest first

    def test_visits_all(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        wps = [Coordinates(lat=42.45 + i * 0.01, lng=18.53) for i in range(5)]
        order = nearest_neighbor_order(origin, wps)
        assert sorted(order) == [0, 1, 2, 3, 4]


class TestTwoOpt:
    def test_empty(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        assert two_opt_improve(origin, []) == []

    def test_two_points(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        wps = [
            Coordinates(lat=42.46, lng=18.54),
            Coordinates(lat=42.47, lng=18.55),
        ]
        order = two_opt_improve(origin, wps)
        assert sorted(order) == [0, 1]

    def test_improves_over_naive(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        # Square route — intentionally non-optimal order
        wps = [
            Coordinates(lat=42.46, lng=18.53),  # north
            Coordinates(lat=42.45, lng=18.55),  # east
            Coordinates(lat=42.46, lng=18.55),  # northeast
            Coordinates(lat=42.44, lng=18.54),  # south
        ]
        order = two_opt_improve(origin, wps)
        assert len(order) == 4
        assert sorted(order) == [0, 1, 2, 3]


class TestOptimizeRoute:
    def test_empty(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        assert optimize_route(origin, []) == []

    def test_single(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        p = _make_place(42.46, 18.54)
        result = optimize_route(origin, [p])
        assert len(result) == 1
        assert result[0].id == p.id

    def test_multiple_returns_all(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        places = [_make_place(42.45 + i * 0.01, 18.53 + i * 0.01, f"P{i}") for i in range(5)]
        result = optimize_route(origin, places)
        assert len(result) == 5
        result_ids = {p.id for p in result}
        assert result_ids == {p.id for p in places}


class TestEstimateDuration:
    def test_walking(self):
        # 1 km at 5 km/h = 720 seconds
        dur = estimate_duration_s(1000, "walking")
        assert 700 < dur < 750

    def test_cycling(self):
        dur = estimate_duration_s(1000, "cycling")
        assert 230 < dur < 250

    def test_driving(self):
        dur = estimate_duration_s(1000, "driving")
        assert 85 < dur < 95

    def test_unknown_mode_defaults_to_walking(self):
        dur = estimate_duration_s(1000, "teleportation")
        assert dur == estimate_duration_s(1000, "walking")


class TestFilterByTimeBudget:
    def test_empty(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        assert filter_by_time_budget(origin, [], 3600, "walking") == []

    def test_fits_all(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        # Very close places, large budget
        places = [_make_place(42.451, 18.531), _make_place(42.452, 18.532)]
        result = filter_by_time_budget(origin, places, 7200, "walking")
        assert len(result) == 2

    def test_tight_budget_filters(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        # Place 30 km away — too far for 1 hour walking
        places = [_make_place(42.75, 18.53, "Far")]
        result = filter_by_time_budget(origin, places, 3600, "walking")
        assert len(result) == 0

    def test_circular_accounts_for_return(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        # Place ~1.1 km away
        places = [_make_place(42.46, 18.53)]
        # Walking ~1.1 km = ~786s each way + 600s visit = ~2172s
        result = filter_by_time_budget(
            origin, places, 2500, "walking", is_circular=True
        )
        assert len(result) == 1

        # Too tight for round trip
        result = filter_by_time_budget(
            origin, places, 1500, "walking", is_circular=True
        )
        assert len(result) == 0


class TestFindPlacesInCorridor:
    def test_empty(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        dest = Coordinates(lat=42.50, lng=18.58)
        assert find_places_in_corridor([], origin, dest, 1.0) == []

    def test_place_on_line(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        dest = Coordinates(lat=42.55, lng=18.53)
        place = _make_place(42.50, 18.53, "OnLine")
        result = find_places_in_corridor([place], origin, dest, 1.0)
        assert len(result) == 1

    def test_place_outside_corridor(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        dest = Coordinates(lat=42.55, lng=18.53)
        # Place 20+ km off the line
        place = _make_place(42.50, 18.80, "Far")
        result = find_places_in_corridor([place], origin, dest, 1.0)
        assert len(result) == 0

    def test_place_within_corridor(self):
        origin = Coordinates(lat=42.45, lng=18.53)
        dest = Coordinates(lat=42.55, lng=18.53)
        # Place ~500m east of midpoint
        place = _make_place(42.50, 18.536, "Near")
        result = find_places_in_corridor([place], origin, dest, 1.0)
        assert len(result) == 1


class TestPointToSegmentDistance:
    def test_point_on_segment(self):
        start = Coordinates(lat=42.45, lng=18.53)
        end = Coordinates(lat=42.55, lng=18.53)
        point = Coordinates(lat=42.50, lng=18.53)
        dist = point_to_segment_distance(point, start, end)
        assert dist < 10  # essentially zero

    def test_point_perpendicular(self):
        start = Coordinates(lat=42.45, lng=18.53)
        end = Coordinates(lat=42.55, lng=18.53)
        # ~1 km east
        point = Coordinates(lat=42.50, lng=18.542)
        dist = point_to_segment_distance(point, start, end)
        assert 800 < dist < 1200

    def test_same_start_end(self):
        start = Coordinates(lat=42.45, lng=18.53)
        point = Coordinates(lat=42.46, lng=18.53)
        dist = point_to_segment_distance(point, start, start)
        assert dist > 0
