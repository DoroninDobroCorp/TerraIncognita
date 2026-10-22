"""Gamification hardening: XP/achievement farming, fog geometry & units, stale progress."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.journal import PlaceNote, PlaceRating, Visit, VisitStatus
from app.models.place import Coordinates
from app.services import gamification as gam
from app.services import journal

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _fresh():
    journal._reset_store()
    gam._reset_store()
    yield
    journal._reset_store()
    gam._reset_store()


def _visit(i: int, place: str | None = None, lat=42.45, lng=18.53, when=None, name="P") -> Visit:
    return Visit(
        id=f"visit_{i}", place_id=place or f"place_{i}", place_name=name,
        status=VisitStatus.VISITED, coordinates=Coordinates(lat=lat, lng=lng),
        visited_at=when or datetime.now(UTC),
    )


def _unlocked(response) -> set[str]:
    return {a.definition.id for a in response.new_achievements}


# ── farming ──────────────────────────────────────────────────────


class TestXpFarming:
    async def test_checking_in_at_the_same_place_again_earns_nothing(self):
        await journal.create_visit("same", 42.45, 18.53, place_name="Same")
        first_xp = (await gam.get_explorer_profile()).total_xp
        assert first_xp > 0

        for _ in range(11):
            await journal.create_visit("same", 42.45, 18.53, place_name="Same")

        profile = await gam.get_explorer_profile()
        assert profile.total_xp == first_xp
        achievements, _, _ = await gam.get_achievements()
        done = {a.definition.id for a in achievements if a.completed}
        assert "visits_10" not in done, "12 check-ins at ONE place are not 10 places"

    async def test_ten_distinct_places_still_unlock_visits_10(self):
        visits = [_visit(i) for i in range(10)]
        assert "visits_10" in _unlocked(await gam.check_achievements(visits, []))

    async def test_replaying_the_same_visit_hook_pays_once(self):
        v = _visit(1)
        first = await gam.award_visit_xp(v, [])
        again = await gam.award_visit_xp(v, [])
        assert first.xp_earned > 0
        assert again.xp_earned == 0
        assert again.total_xp == first.total_xp

    async def test_streak_bonus_is_paid_once_per_day_not_per_visit(self):
        a = await gam.award_visit_xp(_visit(1), [], streak_days=5)
        b = await gam.award_visit_xp(_visit(2), [], streak_days=5)
        assert a.xp_earned == gam.XP_REWARDS["visit_new"] + 5 * gam.XP_REWARDS["streak_bonus"]
        assert b.xp_earned == gam.XP_REWARDS["visit_new"]

    async def test_a_note_pays_its_bonus_once(self):
        v = _visit(1)
        note = PlaceNote(id="n1", visit_id=v.id, place_id=v.place_id, text="hi",
                         rating=PlaceRating(atmosphere=4))
        first = await gam.award_visit_xp(v, [note])
        # same note again with another visit of the same place
        second = await gam.award_visit_xp(_visit(2, place=v.place_id), [note])
        assert first.xp_earned > gam.XP_REWARDS["visit_new"]
        assert second.xp_earned == 0

    async def test_category_achievements_count_places_not_notes(self):
        """Ten notes tagged 'abandoned' on ONE place are not ten abandoned places."""
        visits = [_visit(0)]
        notes = [PlaceNote(id=f"n{i}", visit_id="visit_0", place_id="place_0", tags=["abandoned"])
                 for i in range(10)]
        result = await gam.check_achievements(visits, notes)
        assert "abandoned_10" not in _unlocked(result)

    async def test_xp_event_log_is_bounded_but_total_is_kept(self, monkeypatch):
        monkeypatch.setattr(gam, "MAX_XP_EVENTS", 5)
        for i in range(20):
            await gam._award_xp(10, "t", f"e{i}")
        events, total = await gam.get_xp_history(limit=100)
        assert total == 5 and len(events) == 5
        assert (await gam.get_explorer_profile()).total_xp == 200


class TestAchievementProgress:
    async def test_progress_is_refreshed_on_every_check(self):
        await gam.check_achievements([_visit(i) for i in range(3)], [])
        ach, _, _ = await gam.get_achievements()
        p3 = next(a for a in ach if a.definition.id == "visits_10").progress_percent
        await gam.check_achievements([_visit(i) for i in range(6)], [])
        ach, _, _ = await gam.get_achievements()
        p6 = next(a for a in ach if a.definition.id == "visits_10").progress_percent
        assert p3 == 30.0
        assert p6 == 60.0

    async def test_progress_survives_a_restart(self):
        await gam.check_achievements([_visit(i) for i in range(5)], [])
        gam._loaded = False
        gam._achievements.clear()
        ach, _, _ = await gam.get_achievements()
        assert next(a for a in ach if a.definition.id == "visits_10").progress_percent == 50.0

    async def test_hidden_achievements_stay_masked_after_the_first_check(self):
        await gam.check_achievements([_visit(0)], [])
        ach, _, _ = await gam.get_achievements()
        for a in ach:
            if a.definition.hidden and not a.completed:
                assert a.definition.name == "???"
                assert a.definition.description.startswith("Hidden")


class TestNightOwlUsesLocalTime:
    """visited_at is UTC; 'between midnight and 5 AM' must be the explorer's local time."""

    async def test_late_evening_utc_in_montenegro_is_after_midnight_locally(self):
        v = _visit(1, lng=18.5, when=datetime(2026, 1, 15, 23, 30, tzinfo=UTC))  # ~00:44 local
        assert "night_explorer" in _unlocked(await gam.check_achievements([v], []))

    async def test_small_hours_utc_that_are_already_morning_locally_do_not_count(self):
        v = _visit(1, lng=18.5, when=datetime(2026, 1, 15, 4, 30, tzinfo=UTC))  # ~05:44 local
        got = _unlocked(await gam.check_achievements([v], []))
        assert "night_explorer" not in got
        assert "early_bird" in got  # 05:44 is "before 6 AM"


# ── fog of war ───────────────────────────────────────────────────


def _span_m(keys: list[str], lat: float, lng: float) -> tuple[float, float]:
    """Max east-west and north-south reach (metres) of the revealed cell centres."""
    ew = ns = 0.0
    for k in keys:
        clat, clng = gam._cell_coords(k)
        ew = max(ew, abs(clng - lng) * 111_195 * math.cos(math.radians(lat)))
        ns = max(ns, abs(clat - lat) * 111_195)
    return ew, ns


class TestFogGeometry:
    def test_east_west_reach_is_not_cut_short_at_high_latitude(self):
        lat, lng, r = 60.0, 10.0, 400.0
        keys = gam._cells_in_radius(lat, lng, r)
        ew, ns = _span_m(keys, lat, lng)
        # a 0.001-degree cell is only ~55 m wide at 60N: the old square of +-4 cells
        # (4 * 0.001 deg = 222 m) never got near the requested radius
        assert ew > 0.8 * r
        assert ns > 0.8 * r

    def test_every_returned_cell_is_within_the_radius(self):
        lat, lng, r = 42.45, 18.53, 300.0
        for k in gam._cells_in_radius(lat, lng, r):
            clat, clng = gam._cell_coords(k)
            # cell centre may be up to half a cell (~80 m) away from the probe offset point
            assert gam._haversine_m(lat, lng, clat, clng) <= r + 100

    async def test_explored_area_uses_real_cell_size(self):
        """0.001 deg is ~111 m, not 11 m: a 500 m disc is ~0.8 km2, not 0.008 km2."""
        res = await gam.reveal_fog([(42.45, 18.53, None)], radius_m=500)
        assert 0.5 < res.total_explored_area_km2 < 1.2

    async def test_region_stats_never_exceed_100_percent(self):
        await gam.reveal_fog([(60.0, 10.0, None)], radius_m=500)
        stats = await gam.get_fog_region(60.0, 10.0, 0.5)
        assert 0 < stats.coverage_percent <= 100.0

    async def test_region_stats_for_a_large_radius_are_cheap(self, monkeypatch):
        calls = 0
        real = gam._haversine_m

        def counting(*args):
            nonlocal calls
            calls += 1
            return real(*args)

        monkeypatch.setattr(gam, "_haversine_m", counting)
        stats = await gam.get_fog_region(42.45, 18.53, 50.0)  # the API allows 50 km
        assert stats.total_cells > 100_000
        assert calls < 20_000, f"{calls} haversine calls for one request (event-loop DoS)"

    async def test_total_cell_count_is_capped(self, monkeypatch):
        monkeypatch.setattr(gam, "MAX_FOG_CELLS", 40)
        res = await gam.reveal_fog([(42.45 + i * 0.01, 18.53, None) for i in range(20)], radius_m=300)
        assert res.total_explored_cells == 40

    async def test_reveal_with_nothing_new_does_not_rewrite_the_store(self, monkeypatch):
        await gam.reveal_fog([(42.45, 18.53, None)], radius_m=50)
        saves = 0
        real = gam._save_fog

        def counting():
            nonlocal saves
            saves += 1
            real()

        monkeypatch.setattr(gam, "_save_fog", counting)
        await gam.reveal_fog([(42.45, 18.53, None)], radius_m=50)
        assert saves == 0

    def test_cell_keys_wrap_at_the_antimeridian(self):
        assert gam._cell_key(10.0, 180.0005) == gam._cell_key(10.0, -179.9995)


# ── API ──────────────────────────────────────────────────────────


class TestGamificationApi:
    @pytest.mark.parametrize("qs", ["limit=0", "limit=100000", "offset=-1"])
    def test_xp_history_paging_is_validated(self, qs):
        assert client.get(f"/api/explorer/xp-history?{qs}").status_code == 422

    def test_fog_cells_response_is_bounded(self):
        client.post("/api/fog/reveal", json={"points": [{"lat": 42.45, "lng": 18.53}], "radius_m": 500})
        body = client.get("/api/fog/cells?lat=42.45&lng=18.53&radius_km=5&limit=10").json()
        assert len(body["cells"]) == 10
        assert body["total"] > 10
        assert body["truncated"] is True

    def test_old_visits_can_be_backdated_but_not_into_the_future_for_streaks(self):
        """Visits are stamped server-side: a client cannot choose visited_at."""
        r = client.post("/api/visits", json={
            "place_id": "p", "lat": 1, "lng": 1, "visited_at": "2999-01-01T00:00:00Z"})
        assert r.status_code == 200
        assert datetime.fromisoformat(r.json()["visited_at"]) < datetime.now(UTC) + timedelta(minutes=1)
