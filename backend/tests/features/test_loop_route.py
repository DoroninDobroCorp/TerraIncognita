"""Loop route: geometry/ordering helpers, stop selection properties, endpoint, GPX/KML round trip."""

from __future__ import annotations

import math
import random
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.middleware import RATE_RULES
from app.models.loop_route import LoopRouteRequest
from app.models.place import Coordinates, DiscoverResponse, Place, PlaceCategory, PlaceSource
from app.models.route import Route
from app.services import loop_route
from app.services.loop_route import (
    LoopBudget,
    LoopParamsError,
    NotEnoughPlacesError,
    angular_order,
    bearing_deg,
    best_order,
    build_loop_route,
    loop_length_m,
    select_stops,
    stop_score,
)
from app.services.route_export import export_gpx, export_kml
from app.services.route_optimizer import distance_m

client = TestClient(app)

START = Coordinates(lat=42.45, lng=18.53)
WALK_MS = 5.0 / 3.6
C = PlaceCategory


def offset(lat0: float, lng0: float, dist_m: float, bearing: float) -> tuple[float, float]:
    """The point ``dist_m`` metres from (lat0, lng0) on a compass bearing (flat-earth)."""
    rad = math.radians(bearing)
    lat = lat0 + dist_m * math.cos(rad) / 111_320.0
    lng = lng0 + dist_m * math.sin(rad) / (111_320.0 * math.cos(math.radians(lat0)))
    return lat, lng


def mk(pid: str, dist_m: float, bearing: float, conf: float = 0.7, *cats: C, name: str | None = None) -> Place:
    lat, lng = offset(START.lat, START.lng, dist_m, bearing)
    return Place(
        id=pid, source=PlaceSource.OSM, name=name or f"Place {pid}",
        categories=list(cats) or [C.LANDMARK], confidence=conf,
        coordinates=Coordinates(lat=lat, lng=lng),
    )


def scene(n: int, rng: random.Random, max_m: float = 8000, min_m: float = 300) -> list[Place]:
    return [
        mk(f"s{i}", rng.uniform(min_m, max_m), rng.uniform(0, 360), rng.uniform(0.3, 1.0),
           rng.choice([C.RUINS, C.LANDMARK, C.CAVE, C.PARK]))
        for i in range(n)
    ]


def budget(usable_h: float = 3.0, visit_min: float = 10.0, speed_ms: float = WALK_MS) -> LoopBudget:
    return LoopBudget(speed_ms=speed_ms, visit_s=visit_min * 60, usable_s=usable_h * 3600)


def tour_time(stops: list[Place], b: LoopBudget) -> float:
    return b.time_s(loop_length_m(START, stops), len(stops))


def sector_of(p: Place, sectors: int) -> int:
    return int(bearing_deg(START, p.coordinates) // (360 / sectors)) % sectors


def discover_returning(*pages: list[Place]):
    responses = [DiscoverResponse(places=p, total=len(p), has_more=False) for p in pages]
    return patch("app.services.loop_route.discover", new_callable=AsyncMock, side_effect=responses)


# ── helpers ─────────────────────────────────────────────────────


class TestGeometry:
    @pytest.mark.parametrize(("b", "expected"), [(0, 0), (90, 90), (180, 180), (270, 270), (45, 45)])
    def test_bearing_of_offsets_round_trips(self, b, expected):
        lat, lng = offset(START.lat, START.lng, 1000, b)
        assert bearing_deg(START, Coordinates(lat=lat, lng=lng)) == pytest.approx(expected, abs=0.5)

    def test_bearing_is_in_0_360(self):
        for b in range(0, 360, 15):
            lat, lng = offset(START.lat, START.lng, 500, b)
            assert 0 <= bearing_deg(START, Coordinates(lat=lat, lng=lng)) < 360

    def test_loop_length_includes_the_closing_leg(self):
        p = mk("a", 1000, 90)
        assert loop_length_m(START, [p]) == pytest.approx(2 * distance_m(START, p.coordinates))
        a, b = mk("a", 1000, 0), mk("b", 1000, 90)
        expected = 1000 + distance_m(a.coordinates, b.coordinates) + 1000
        assert loop_length_m(START, [a, b]) == pytest.approx(expected, rel=0.01)

    def test_loop_length_of_nothing_is_zero(self):
        assert loop_length_m(START, []) == 0.0

    def test_angular_order_walks_around_the_start(self):
        ring = [mk(f"r{b}", 1000, b) for b in (270, 0, 180, 90)]
        order = angular_order(START, ring)
        bearings = [round(bearing_deg(START, p.coordinates)) for p in order]
        # a rotation of 0, 90, 180, 270 (the cut position is chosen for the shortest tour)
        assert sorted(bearings) == [0, 90, 180, 270]
        steps = [(bearings[(i + 1) % 4] - bearings[i]) % 360 for i in range(4)]
        assert sorted(steps) == [90, 90, 90, 90]

    def test_best_order_trivial_inputs(self):
        assert best_order(START, []) == []
        one = [mk("a", 500, 10)]
        assert best_order(START, one) == one

    def test_best_order_is_never_longer_than_its_seed_or_the_angular_sweep(self):
        rng = random.Random(11)
        for _ in range(40):
            stops = scene(rng.randint(3, 8), rng, 5000)
            shuffled = list(stops)
            rng.shuffle(shuffled)
            seeded = best_order(START, stops, seeds=[shuffled])
            assert loop_length_m(START, seeded) <= loop_length_m(START, shuffled) + 1e-6
            plain = best_order(START, stops)
            assert loop_length_m(START, plain) <= loop_length_m(START, angular_order(START, stops)) + 1e-6

    def test_best_order_keeps_all_stops(self):
        stops = scene(7, random.Random(2))
        assert sorted(p.id for p in best_order(START, stops)) == sorted(p.id for p in stops)


class TestBudget:
    def test_uses_settings_speed_visit_time_and_margin(self, monkeypatch):
        monkeypatch.setattr(settings, "route_walking_speed_kmh", 6.0)
        monkeypatch.setattr(settings, "route_cycling_speed_kmh", 18.0)
        monkeypatch.setattr(settings, "route_visit_time_minutes", 12.0)
        monkeypatch.setattr(settings, "loop_safety_margin", 0.2)
        walk = LoopBudget.for_request(2.0, "walking")
        assert walk.speed_ms == pytest.approx(6 / 3.6)
        assert walk.visit_s == 720
        assert walk.usable_s == pytest.approx(2 * 3600 * 0.8)
        assert LoopBudget.for_request(2.0, "cycling").speed_ms == pytest.approx(5.0)

    def test_time_and_fit(self):
        b = budget(usable_h=1.0, visit_min=10)
        assert b.time_s(0, 3) == 1800
        assert b.time_s(WALK_MS * 600, 0) == pytest.approx(600)
        assert b.fits(WALK_MS * 1800, 2)  # 1800 s walking + 1200 s visiting = 3000 <= 3600
        assert not b.fits(WALK_MS * 2500, 2)

    def test_reach_is_half_the_time_left_after_one_visit(self):
        b = budget(usable_h=1.0, visit_min=10)
        assert b.reach_m == pytest.approx((3600 - 600) * WALK_MS / 2)


# ── selection properties ────────────────────────────────────────


class TestSelectStops:
    GRID = [
        (seed, usable_h, speed, max_stops)
        for seed in range(6)
        for usable_h, speed, max_stops in [
            (0.5, WALK_MS, 5), (1.0, WALK_MS, 3), (2.0, WALK_MS, 8),
            (3.0, WALK_MS, 4), (6.0, WALK_MS, 8), (2.0, 15 / 3.6, 6), (8.0, 15 / 3.6, 8),
        ]
    ]

    @pytest.mark.parametrize(("seed", "usable_h", "speed", "max_stops"), GRID)
    def test_properties(self, seed, usable_h, speed, max_stops):
        rng = random.Random(seed * 1000 + max_stops)
        places = scene(rng.randint(8, 70), rng, max_m=25_000 if speed > 2 else 9_000)
        b = budget(usable_h, 10, speed)
        stops = select_stops(START, places, b, max_stops)
        if stops is None:
            return  # allowed: nothing fits (checked separately below)
        ids = [p.id for p in stops]
        assert 2 <= len(stops) <= max_stops
        assert len(set(ids)) == len(ids), "no duplicate stops"
        assert set(ids) <= {p.id for p in places}
        assert tour_time(stops, b) <= b.usable_s, "travel + visiting fits the budget"
        # the order is not worse than naive orders of the same stops
        naive = [list(reversed(stops)), sorted(stops, key=lambda p: p.id),
                 sorted(stops, key=stop_score, reverse=True), angular_order(START, stops)]
        for _ in range(8):
            shuffled = list(stops)
            rng.shuffle(shuffled)
            naive.append(shuffled)
        ours = loop_length_m(START, stops)
        assert ours <= min(loop_length_m(START, n) for n in naive) + 1e-6

    def test_most_realistic_budgets_do_produce_a_loop(self):
        rng = random.Random(5)
        places = scene(40, rng, 6000)
        for usable_h in (1.0, 2.0, 4.0, 8.0):
            assert select_stops(START, places, budget(usable_h), 5) is not None

    def test_more_time_never_means_fewer_stops_in_a_fixed_scene(self):
        places = scene(40, random.Random(8), 6000)
        counts = [len(select_stops(START, places, budget(h), 8) or []) for h in (1, 2, 3, 5, 8)]
        assert counts == sorted(counts)

    def test_stops_are_spread_around_the_start(self):
        # six excellent places clumped north-east, one decent place in each other quadrant
        clump = [mk(f"ne{i}", 1500 + 40 * i, 20 + 8 * i, 0.95) for i in range(6)]
        others = [mk("nw", 1500, 315, 0.5), mk("se", 1500, 135, 0.5), mk("sw", 1500, 225, 0.5)]
        stops = select_stops(START, clump + others, budget(4.0), 4)
        assert stops is not None and len(stops) == 4
        assert {sector_of(p, 4) for p in stops} == {0, 1, 2, 3}
        assert sum(p.id.startswith("ne") for p in stops) == 1

    def test_quality_wins_inside_a_sector(self):
        good, meh = mk("good", 1500, 40, 0.95), mk("meh", 1500, 50, 0.4)
        other = mk("other", 1500, 220, 0.6)
        stops = select_stops(START, [meh, good, other], budget(3.0), 2)
        assert {p.id for p in stops} == {"good", "other"}

    def test_closer_wins_at_equal_quality(self):
        near, far = mk("near", 800, 40, 0.8), mk("far", 3500, 45, 0.8)
        other = mk("other", 1200, 220, 0.6)
        stops = select_stops(START, [far, near, other], budget(3.0), 2)
        assert {p.id for p in stops} == {"near", "other"}

    def test_trims_a_far_stop_when_everything_does_not_fit(self):
        near = [mk(f"n{i}", 500, i * 90 + 10, 0.6) for i in range(3)]
        far = mk("far", 3200, 300, 0.9)
        b = budget(2.0)  # 4 visits = 40 min; the far trip alone needs ~77 min of walking
        assert not b.fits(loop_length_m(START, [*near, far]), 4)
        stops = select_stops(START, [*near, far], b, 4)
        assert stops is not None and tour_time(stops, b) <= b.usable_s
        assert "far" not in {p.id for p in stops}
        assert len(stops) == 3

    def test_falls_back_to_the_best_affordable_pair(self):
        # one sector only -> a single sector pick; the pair fallback + fill complete the loop
        cluster = [mk(f"c{i}", 1000 + 100 * i, 30 + 3 * i, 0.6 + i * 0.05) for i in range(5)]
        stops = select_stops(START, cluster, budget(3.0), 4)
        assert stops is not None and len(stops) == 4  # pair first, then filled up
        assert tour_time(stops, budget(3.0)) <= 3 * 3600

    def test_pair_fallback_picks_an_affordable_pair_when_the_best_ones_do_not_fit(self):
        b = budget(1.0, visit_min=10)  # 3600 s: 2 visits leave 2400 s ~ 3.3 km of walking
        far_a, far_b = mk("fa", 1400, 0, 0.95), mk("fb", 1400, 180, 0.95)  # 2.8 km out-and-back
        near_a, near_b = mk("na", 300, 90, 0.3), mk("nb", 300, 270, 0.3)
        stops = select_stops(START, [far_a, far_b, near_a, near_b], b, 2)
        assert stops is not None and tour_time(stops, b) <= b.usable_s
        assert len(stops) == 2

    def test_none_when_fewer_than_two_places_are_reachable(self):
        b = budget(1.0)
        reachable, unreachable = mk("a", 500, 0), mk("b", 20_000, 90)
        assert select_stops(START, [reachable, unreachable], b, 4) is None
        assert select_stops(START, [], b, 4) is None
        assert select_stops(START, [reachable], b, 4) is None

    def test_none_when_no_pair_fits(self):
        b = budget(1.0, visit_min=10)  # reach 1.25 km; two stops at the limit need ~5 km
        edge_a, edge_b = mk("a", 1200, 0), mk("b", 1200, 180)
        assert not b.fits(loop_length_m(START, [edge_a, edge_b]), 2)
        assert select_stops(START, [edge_a, edge_b], b, 3) is None

    def test_near_duplicates_are_not_two_stops(self):
        twin_a, twin_b = mk("twin_a", 1000, 40, 0.9), mk("twin_b", 1000, 40.01, 0.89)
        assert distance_m(twin_a.coordinates, twin_b.coordinates) < 75
        others = [mk("o1", 1000, 160, 0.5), mk("o2", 1000, 280, 0.5)]
        for max_stops in (3, 4, 5):
            stops = select_stops(START, [twin_a, twin_b, *others], budget(5.0), max_stops)
            assert stops is not None
            assert not {"twin_a", "twin_b"} <= {p.id for p in stops}, "both twins were chosen"

    def test_fills_the_time_up_to_max_stops_when_places_are_close(self):
        # every place is in the first two of six 60-degree sectors: the spread step picks
        # two, the fill step must add the rest
        places = [mk(f"p{i}", 600 + 20 * i, 5 + i * 9, 0.6) for i in range(9)]
        assert {sector_of(p, 6) for p in places} == {0, 1}
        stops = select_stops(START, places, budget(5.0), 6)
        assert stops is not None and len(stops) == 6

    def test_fill_stops_when_the_next_stop_would_break_the_budget(self):
        places = [mk(f"p{i}", 1500 + 20 * i, 5 + i * 9, 0.6) for i in range(9)]
        b = budget(1.5)
        stops = select_stops(START, places, b, 8)
        assert stops is not None and 2 <= len(stops) < 8
        assert tour_time(stops, b) <= b.usable_s

    def test_never_exceeds_max_stops(self):
        places = [mk(f"p{i}", 600, i * 15, 0.6) for i in range(24)]
        for max_stops in (2, 3, 8):
            assert len(select_stops(START, places, budget(8.0), max_stops)) <= max_stops

    def test_result_does_not_depend_on_input_order(self):
        places = scene(30, random.Random(4), 5000)
        expected = [p.id for p in select_stops(START, places, budget(3.0), 5)]
        rng = random.Random(0)
        for _ in range(5):
            rng.shuffle(places)
            assert [p.id for p in select_stops(START, places, budget(3.0), 5)] == expected

    def test_duplicate_ids_in_the_input_are_ignored(self):
        a, b = mk("a", 800, 0), mk("b", 800, 180)
        stops = select_stops(START, [a, a, b, b], budget(3.0), 4)
        assert sorted(p.id for p in stops) == ["a", "b"]

    def test_uses_the_existing_optimizer_helper(self):
        places = scene(20, random.Random(9), 4000)
        with patch.object(loop_route, "optimize_route", wraps=loop_route.optimize_route) as spy:
            select_stops(START, places, budget(3.0), 5)
        assert spy.called
        assert all(call.kwargs.get("is_circular") is True for call in spy.call_args_list)

    def test_named_places_beat_unnamed_ones_of_equal_quality(self):
        named = mk("named", 1500, 40, 0.6)
        unnamed = mk("unnamed", 1500, 50, 0.6)
        unnamed.name = None
        other = mk("other", 1500, 220, 0.6)
        assert stop_score(named) > stop_score(unnamed)
        stops = select_stops(START, [unnamed, named, other], budget(3.0), 2)
        assert {p.id for p in stops} == {"named", "other"}

    def test_stop_score_prefers_unusual_and_confident_places(self):
        assert stop_score(mk("a", 1, 0, 0.9, C.RUINS)) > stop_score(mk("b", 1, 0, 0.9, C.MUSEUM))
        assert stop_score(mk("a", 1, 0, 0.9)) > stop_score(mk("b", 1, 0, 0.4))


# ── service level ───────────────────────────────────────────────


def request(**kw) -> LoopRouteRequest:
    return LoopRouteRequest(**{"lat": START.lat, "lng": START.lng, "duration_hours": 3, **kw})


def response_times(resp, req: LoopRouteRequest) -> tuple[float, float]:
    """(travel + visiting seconds from the response's own numbers, usable budget)."""
    route = resp.route
    stops = [w for w in route.waypoints if not w.is_origin and not w.is_destination]
    total = route.total_duration_s + len(stops) * settings.route_visit_time_minutes * 60
    return total, req.duration_hours * 3600 * (1 - settings.loop_safety_margin)


class TestBuildLoopRoute:
    PROPERTY_CASES = [
        (seed, hours, mode, max_stops)
        for seed in range(4)
        for hours, mode, max_stops in [
            (0.75, "walking", 3), (1.5, "walking", 5), (3, "walking", 8),
            (4, "cycling", 4), (8, "cycling", 8), (12, "walking", 8),
        ]
    ]

    @pytest.mark.parametrize(("seed", "hours", "mode", "max_stops"), PROPERTY_CASES)
    async def test_loop_properties(self, seed, hours, mode, max_stops):
        rng = random.Random(seed * 31 + max_stops)
        places = scene(rng.randint(10, 60), rng, 14_000 if mode == "cycling" else 6_000)
        req = request(duration_hours=hours, mode=mode, max_stops=max_stops)
        with discover_returning(places, places):
            try:
                resp = await build_loop_route(req)
            except NotEnoughPlacesError:
                return
        route = resp.route
        stops = [w.place for w in route.waypoints if not w.is_origin and not w.is_destination]
        # round trip
        assert route.is_circular
        first, last = route.waypoints[0], route.waypoints[-1]
        assert first.is_origin and last.is_destination
        assert first.place.coordinates == last.place.coordinates == Coordinates(lat=START.lat, lng=START.lng)
        # 2..max_stops distinct places
        assert 2 <= len(stops) <= max_stops
        assert len({p.id for p in stops}) == len(stops)
        assert route.places_count == len(stops)
        # time budget, by the response's own numbers
        used, usable = response_times(resp, req)
        assert used <= usable
        # segments chain from start through every stop and back
        assert len(route.segments) == len(stops) + 1
        assert route.segments[0].from_point == route.segments[-1].to_point
        assert route.total_distance_m == pytest.approx(sum(s.distance_m for s in route.segments), abs=0.5)
        # not worse than naive orders of the same stops
        ours = loop_length_m(START, stops)
        rng2 = random.Random(1)
        naive = [list(reversed(stops)), sorted(stops, key=lambda p: p.id)]
        for _ in range(8):
            shuffled = list(stops)
            rng2.shuffle(shuffled)
            naive.append(shuffled)
        assert ours <= min(loop_length_m(START, n) for n in naive) + 1e-6

    async def test_durations_use_the_settings_speed_not_the_optimizer_default(self, monkeypatch):
        monkeypatch.setattr(settings, "route_walking_speed_kmh", 4.0)
        places = scene(25, random.Random(1), 3000)
        req = request(duration_hours=3, max_stops=4)
        with discover_returning(places, places):
            resp = await build_loop_route(req)
        for seg in resp.route.segments:
            assert seg.duration_s == pytest.approx(seg.distance_m / (4.0 / 3.6), abs=0.1)
        used, usable = response_times(resp, req)
        assert used <= usable

    async def test_cycling_reaches_further_than_walking(self):
        places = scene(60, random.Random(6), 12_000)
        walk, bike = request(duration_hours=4, max_stops=6), request(duration_hours=4, mode="cycling", max_stops=6)
        with discover_returning(places, places, places, places):
            w = await build_loop_route(walk)
            b = await build_loop_route(bike)
        assert b.route.total_distance_m > w.route.total_distance_m
        assert b.route.transport_mode.value == "cycling"

    async def test_discovery_radius_is_derived_from_the_budget(self):
        places = scene(20, random.Random(3), 3000)
        with discover_returning(places) as mock:
            await build_loop_route(request(duration_hours=3, categories=[C.RUINS], exclude_visited=["x"]))
        req = mock.await_args.args[0]
        assert (req.lat, req.lng, req.limit) == (START.lat, START.lng, 200)
        assert req.categories == [C.RUINS] and req.exclude_visited == ["x"]
        # ~ speed * usable travel time / 3 for a 3 h walk: a few km, not the 50 km maximum
        assert 2.0 < req.radius_km < 6.0
        with discover_returning(places) as short:
            await build_loop_route(request(duration_hours=1))
        assert short.await_args.args[0].radius_km < req.radius_km

    async def test_sparse_area_widens_the_radius_once(self):
        nearly_empty = [mk("only", 300, 0)]
        reachable_further_out = [mk("a", 2500, 0, 0.8), mk("b", 2500, 90, 0.8)]
        with discover_returning(nearly_empty, reachable_further_out) as mock:
            resp = await build_loop_route(request(duration_hours=3, max_stops=3))
        radii = [c.args[0].radius_km for c in mock.await_args_list]
        assert len(radii) == 2 and radii[1] > radii[0]
        assert resp.discovered_places == 2

    async def test_not_enough_places(self):
        with discover_returning([], []):
            with pytest.raises(NotEnoughPlacesError) as info:
                await build_loop_route(request())
        assert "Try" in str(info.value)
        with discover_returning([mk("lonely", 400, 0)], [mk("lonely", 400, 0)]):
            with pytest.raises(NotEnoughPlacesError):
                await build_loop_route(request())

    async def test_too_short_for_two_visits(self, monkeypatch):
        monkeypatch.setattr(settings, "route_visit_time_minutes", 20.0)
        with pytest.raises(LoopParamsError) as info:
            await build_loop_route(request(duration_hours=0.5))
        assert "too short" in str(info.value)

    async def test_summary_and_metadata(self):
        places = scene(20, random.Random(3), 3000)
        with discover_returning(places):
            resp = await build_loop_route(request(duration_hours=3, max_stops=4))
        meta = resp.route.metadata
        assert meta["kind"] == "loop"
        assert meta["total_time_s"] == pytest.approx(meta["travel_time_s"] + meta["visit_time_s"], abs=0.2)
        assert meta["total_time_s"] <= meta["usable_budget_s"] <= meta["duration_budget_s"] == 3 * 3600
        assert "Circular walking route" in resp.summary and "including visits" in resp.summary
        assert resp.navigation_links["google_maps"].startswith("https://www.google.com/maps/dir/")

    async def test_discovered_places_counts_the_pool_not_the_stops(self):
        places = scene(33, random.Random(3), 3000)
        with discover_returning(places):
            resp = await build_loop_route(request(max_stops=3))
        assert resp.discovered_places == 33


# ── endpoint ────────────────────────────────────────────────────


def post_loop(body: dict, *pages: list[Place]):
    with discover_returning(*pages):
        return client.post("/api/route/loop", json=body)


BODY = {"lat": START.lat, "lng": START.lng, "duration_hours": 3, "mode": "walking",
        "categories": [], "max_stops": 5}


class TestLoopEndpoint:
    def test_returns_a_route_response(self):
        places = scene(30, random.Random(1), 4000)
        resp = post_loop(BODY, places)
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"route", "discovered_places", "summary", "navigation_links"}
        route = data["route"]
        assert route["is_circular"] is True
        assert route["waypoints"][0]["is_origin"] and route["waypoints"][-1]["is_destination"]
        assert route["waypoints"][0]["place"]["coordinates"] == route["waypoints"][-1]["place"]["coordinates"]
        assert route["transport_mode"] == "walking"
        assert 2 <= route["places_count"] <= 5

    def test_cycling_mode_and_minimal_body(self):
        places = scene(30, random.Random(1), 9000)
        resp = post_loop({"lat": START.lat, "lng": START.lng, "mode": "cycling"}, places)
        assert resp.status_code == 200 and resp.json()["route"]["transport_mode"] == "cycling"

    @pytest.mark.parametrize(
        "patch_body",
        [
            {"duration_hours": 0.49}, {"duration_hours": 12.01}, {"duration_hours": 0},
            {"duration_hours": "soon"}, {"mode": "driving"}, {"mode": "helicopter"},
            {"max_stops": 1}, {"max_stops": 9}, {"lat": 91}, {"lng": -181},
            {"categories": ["not_a_category"]}, {"exclude_visited": ["x"] * 1001},
        ],
    )
    def test_invalid_params_are_422(self, patch_body):
        assert client.post("/api/route/loop", json={**BODY, **patch_body}).status_code == 422

    @pytest.mark.parametrize("missing", ["lat", "lng"])
    def test_missing_start_is_422(self, missing):
        body = {k: v for k, v in BODY.items() if k != missing}
        assert client.post("/api/route/loop", json=body).status_code == 422

    @pytest.mark.parametrize(("hours", "stops"), [(0.5, 2), (12, 8), (12, 2), (0.5, 8)])
    def test_boundary_values_are_accepted(self, hours, stops):
        places = scene(40, random.Random(2), 2500, 100)
        resp = post_loop({**BODY, "duration_hours": hours, "max_stops": stops}, places, places)
        assert resp.status_code in (200, 404)  # never 422: the bounds themselves are valid

    def test_404_with_a_message_when_there_are_not_enough_places(self):
        resp = post_loop(BODY, [], [])
        assert resp.status_code == 404
        assert "Not enough interesting places" in resp.json()["detail"]

    def test_422_when_the_time_cannot_fit_two_visits(self, monkeypatch):
        monkeypatch.setattr(settings, "route_visit_time_minutes", 25.0)
        resp = client.post("/api/route/loop", json={**BODY, "duration_hours": 0.5})
        assert resp.status_code == 422 and "too short" in resp.json()["detail"]

    def test_failure_is_500_without_details(self):
        with patch(
            "app.services.loop_route.discover",
            new_callable=AsyncMock, side_effect=RuntimeError("overpass key=abc"),
        ):
            resp = client.post("/api/route/loop", json=BODY)
        assert resp.status_code == 500 and "abc" not in resp.text

    def test_rate_limited_with_the_discovery_rules(self):
        rule = next(r for r in RATE_RULES if r.matches("POST", "/api/route/loop"))
        assert rule.name == "discovery"
        assert not rule.matches("GET", "/api/route/loop")
        assert next(r for r in RATE_RULES if r.matches("POST", "/api/route")) is rule

    def test_empty_body_is_422_and_the_sibling_route_endpoint_still_works(self):
        assert client.post("/api/route/loop", json={}).status_code == 422
        assert client.post("/api/route", json={}).status_code == 422


# ── export round trip ───────────────────────────────────────────


GPX = "{http://www.topografix.com/GPX/1/1}"
KML = "{http://www.opengis.net/kml/2.2}"


class TestLoopExport:
    @pytest.fixture
    def loop(self):
        places = scene(30, random.Random(12), 4000)
        resp = post_loop(BODY, places)
        assert resp.status_code == 200
        return resp.json()["route"]

    def test_gpx_through_the_export_endpoint(self, loop):
        resp = client.post("/api/route/export", json={"route": loop, "format": "gpx"})
        assert resp.status_code == 200
        root = ET.fromstring(resp.json()["content"])
        wpts = root.findall(f"{GPX}wpt")
        trkpts = root.findall(f"{GPX}trk/{GPX}trkseg/{GPX}trkpt")
        n = loop["places_count"]
        assert len(wpts) == len(trkpts) == n + 2
        start = (str(START.lat), str(START.lng))
        assert (wpts[0].get("lat"), wpts[0].get("lon")) == start
        assert (wpts[-1].get("lat"), wpts[-1].get("lon")) == start
        assert (trkpts[0].get("lat"), trkpts[0].get("lon")) == (trkpts[-1].get("lat"), trkpts[-1].get("lon"))
        names = [w.findtext(f"{GPX}name") for w in wpts]
        assert names[0] == "Start" and names[-1] == "Return to Start"
        assert all(names[1:-1])
        assert resp.json()["filename"].endswith(".gpx")

    def test_kml_through_the_export_endpoint(self, loop):
        resp = client.post("/api/route/export", json={"route": loop, "format": "kml"})
        assert resp.status_code == 200
        root = ET.fromstring(resp.json()["content"])
        placemarks = root.findall(f".//{KML}Placemark")
        assert len(placemarks) == loop["places_count"] + 2 + 1  # stops + start/return + path
        line = root.find(f".//{KML}LineString/{KML}coordinates").text.split()
        assert len(line) == loop["places_count"] + 2
        assert line[0] == line[-1]
        assert resp.json()["filename"].endswith(".kml")

    def test_exporters_accept_the_parsed_route_directly(self, loop):
        route = Route.model_validate(loop)
        assert route.is_circular
        gpx = ET.fromstring(export_gpx(route))
        kml = ET.fromstring(export_kml(route))
        assert len(gpx.findall(f"{GPX}wpt")) == len(route.waypoints)
        assert kml.find(f".//{KML}LineString") is not None

    def test_the_route_survives_a_json_round_trip_unchanged(self, loop):
        assert Route.model_validate(loop).model_dump(mode="json") == loop


class TestBudgetHardening:
    def test_silly_settings_do_not_crash_or_go_negative(self, monkeypatch):
        monkeypatch.setattr(settings, "route_walking_speed_kmh", 0.0)
        monkeypatch.setattr(settings, "loop_safety_margin", 5.0)
        monkeypatch.setattr(settings, "route_visit_time_minutes", -3.0)
        b = LoopBudget.for_request(2.0, "walking")
        assert b.speed_ms > 0 and b.visit_s == 0 and b.usable_s > 0
        assert b.margin == pytest.approx(0.9)

    def test_negative_margin_is_treated_as_zero(self, monkeypatch):
        monkeypatch.setattr(settings, "loop_safety_margin", -1.0)
        assert LoopBudget.for_request(1.0, "walking").usable_s == 3600

    def test_too_short_message_names_a_workable_duration(self, monkeypatch):
        monkeypatch.setattr(settings, "route_visit_time_minutes", 20.0)
        monkeypatch.setattr(settings, "loop_safety_margin", 0.1)
        resp = client.post("/api/route/loop", json={**BODY, "duration_hours": 0.5})
        assert resp.status_code == 422
        assert "at least 50 minutes" in resp.json()["detail"]  # ceil(40 / 0.9) + 5
