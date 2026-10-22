"""Route builder / optimizer / export hardening."""

from __future__ import annotations

import random
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.place import Coordinates, DiscoverResponse, Place, PlaceSource
from app.models.route import ExploreRouteRequest, RouteRequest, TransportMode
from app.services import route_builder
from app.services.route_export import export_gpx, export_kml
from app.services.route_optimizer import (
    distance_m,
    filter_by_time_budget,
    nearest_neighbor_order,
    optimize_route,
    two_opt_improve,
)

client = TestClient(app, raise_server_exceptions=False)


def _place(pid, lat, lng, name=None, description=None) -> Place:
    return Place(id=pid, source=PlaceSource.OSM, name=name or pid, description=description,
                 coordinates=Coordinates(lat=lat, lng=lng))


def _cost(origin, pts, order, end=None) -> float:
    path = [origin] + [pts[i] for i in order] + [end or origin]
    return sum(distance_m(a, b) for a, b in zip(path, path[1:], strict=False))


# ── optimizer ────────────────────────────────────────────────────


class TestTwoOpt:
    def test_dense_area_does_not_block_the_event_loop(self):
        """Every candidate reversal re-summed the whole tour: O(n^3) per pass, ~3 s for the
        120 places a dense city returns, ~15 s for the 200 that discovery may return."""
        rnd = random.Random(1)
        origin = Coordinates(lat=42.4, lng=18.7)
        pts = [Coordinates(lat=42.4 + rnd.random() * 0.1, lng=18.7 + rnd.random() * 0.1)
               for _ in range(120)]
        t0 = time.perf_counter()
        order = two_opt_improve(origin, pts)
        elapsed = time.perf_counter() - t0
        assert sorted(order) == list(range(120))
        assert elapsed < 1.0, f"2-opt took {elapsed:.1f}s for 120 points"

    def test_result_is_never_worse_than_nearest_neighbour(self):
        rnd = random.Random(7)
        origin = Coordinates(lat=42.4, lng=18.7)
        for n in (3, 5, 9, 25):
            pts = [Coordinates(lat=42.4 + rnd.random() * 0.05, lng=18.7 + rnd.random() * 0.05)
                   for _ in range(n)]
            nn = nearest_neighbor_order(origin, pts)
            opt = two_opt_improve(origin, pts)
            assert sorted(opt) == list(range(n))
            assert _cost(origin, pts, opt) <= _cost(origin, pts, nn) + 1e-6

    def test_point_to_point_order_with_two_stops_is_optimised(self):
        """n <= 2 returned the input order unexamined."""
        o, d = Coordinates(lat=0, lng=0), Coordinates(lat=0, lng=0.1)
        pts = [Coordinates(lat=0, lng=0.08), Coordinates(lat=0, lng=0.02)]
        assert two_opt_improve(o, pts, destination=d) == [1, 0]

    def test_destination_is_taken_into_account(self):
        o, d = Coordinates(lat=0, lng=0), Coordinates(lat=0, lng=0.1)
        pts = [Coordinates(lat=0.0, lng=0.05), Coordinates(lat=0.0, lng=0.09),
               Coordinates(lat=0.0, lng=0.01), Coordinates(lat=0.0, lng=0.07)]
        order = two_opt_improve(o, pts, destination=d)
        assert order == [2, 0, 3, 1]

    def test_optimize_route_does_not_alias_the_input_list(self):
        places = [_place("a", 0, 0.01)]
        out = optimize_route(Coordinates(lat=0, lng=0), places)
        assert out == places
        assert out is not places


class TestTimeBudget:
    def test_point_to_point_budget_includes_the_leg_to_the_destination(self):
        origin = Coordinates(lat=0, lng=0)
        dest = Coordinates(lat=0, lng=0.1)  # ~11 km = ~2.2 h on foot
        near = _place("near", 0, 0.001)
        kept = filter_by_time_budget(origin, [near], 3600, "walking", destination=dest)
        assert kept == [], "a 1 h budget cannot also cover the 2.2 h walk to the destination"
        kept = filter_by_time_budget(origin, [near], 4 * 3600, "walking", destination=dest)
        assert kept == [near]

    def test_without_destination_behaviour_is_unchanged(self):
        origin = Coordinates(lat=0, lng=0)
        near = _place("near", 0, 0.001)
        assert filter_by_time_budget(origin, [near], 3600, "walking") == [near]

    async def test_built_point_to_point_route_fits_the_requested_time(self, monkeypatch):
        origin, dest = Coordinates(lat=0, lng=0), Coordinates(lat=0, lng=0.05)  # ~5.6 km ~ 66 min
        on_the_way = _place("on_the_way", 0, 0.025)
        detour = _place("detour", 0.04, 0.025)  # ~4.4 km off the line

        async def fake_discover(req):
            return DiscoverResponse(places=[on_the_way, detour], total=2, has_more=False)

        monkeypatch.setattr(route_builder, "discover", fake_discover)
        req = RouteRequest(origin=origin, destination=dest, corridor_width_km=10,
                           max_duration_hours=1.9, transport_mode=TransportMode.WALKING)
        resp = await route_builder.build_route(req)
        names = [w.place.id for w in resp.route.waypoints if not w.is_origin and not w.is_destination]
        assert "on_the_way" in names
        assert "detour" not in names
        visit_s = 600 * len(names)
        assert resp.route.total_duration_s + visit_s <= 1.9 * 3600

    async def test_explore_route_still_works(self, monkeypatch):
        async def fake_discover(req):
            return DiscoverResponse(places=[_place("a", 0.005, 0.0)], total=1, has_more=False)

        monkeypatch.setattr(route_builder, "discover", fake_discover)
        resp = await route_builder.build_explore_route(
            ExploreRouteRequest(origin=Coordinates(lat=0, lng=0), max_duration_hours=2))
        assert resp.route.places_count == 1


# ── reorder ──────────────────────────────────────────────────────


class TestReorder:
    def test_empty_route_is_a_400_not_a_500(self):
        r = client.post("/api/route/reorder", json={
            "route": {"id": "r", "waypoints": []}, "waypoint_order": []})
        assert r.status_code == 400

    def test_oversized_requests_are_rejected(self):
        wp = {"place": {"id": "x", "source": "osm", "coordinates": {"lat": 0, "lng": 0}}, "order": 0}
        r = client.post("/api/route/reorder", json={
            "route": {"id": "r", "waypoints": [wp] * 150}, "waypoint_order": list(range(150))})
        assert r.status_code == 422


# ── export ───────────────────────────────────────────────────────


def _route(name, description=None):
    route = route_builder._build_route_object(
        Coordinates(lat=0, lng=0), Coordinates(lat=0, lng=0.1),
        [_place("x", 0, 0.05, name=name, description=description)], TransportMode.WALKING, False)
    return route


class TestExportWellFormed:
    @pytest.mark.parametrize("fn", [export_gpx, export_kml])
    @pytest.mark.parametrize("name", ["A\x07B", "nul\x00byte", "esc\x1b[0m", "ok ￾", "lone \ud800 surrogate"])
    def test_control_characters_do_not_break_the_xml(self, fn, name):
        doc = fn(_route(name, description=f"d {name}"))
        ET.fromstring(doc.split("\n", 1)[1])  # raises ParseError if not well-formed
        doc.encode("utf-8")  # a lone surrogate would break the HTTP response

    @pytest.mark.parametrize("fn", [export_gpx, export_kml])
    def test_markup_in_names_is_escaped_not_interpreted(self, fn):
        name = '</name><script>alert(1)</script><x a="1">&amp;'
        doc = fn(_route(name, description=name))
        root = ET.fromstring(doc.split("\n", 1)[1])
        assert not [e for e in root.iter() if e.tag.endswith("script")]
        text = "".join(root.itertext())
        assert name in text  # preserved verbatim as text

    def test_api_export_with_control_characters_returns_wellformed_xml(self):
        route = _route("A\x07B").model_dump(mode="json")
        r = client.post("/api/route/export", json={"route": route, "format": "gpx"})
        assert r.status_code == 200
        ET.fromstring(r.json()["content"].split("\n", 1)[1])

    def test_unicode_survives(self):
        doc = export_gpx(_route("Тврђава Могрен — šuma"))
        assert "Тврђава Могрен — šuma" in doc
