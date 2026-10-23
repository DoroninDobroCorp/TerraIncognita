"""Streak: pure computation (midnight boundaries, gaps, duplicates, future) and the endpoint."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.journal import Visit
from app.models.place import Coordinates
from app.services.streak import STREAK_WINDOW_DAYS, compute_streak, local_day

client = TestClient(app)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)  # a Thursday, noon UTC


def at(days_ago: int, hour: int = 10, minute: int = 0) -> datetime:
    """A UTC timestamp ``days_ago`` days before NOW's date."""
    return datetime(2026, 10, 22, hour, minute, tzinfo=UTC) - timedelta(days=days_ago)


class TestLocalDay:
    def test_utc_default(self):
        assert local_day(datetime(2026, 10, 22, 23, 59, 59, tzinfo=UTC)) == date(2026, 10, 22)
        assert local_day(datetime(2026, 10, 23, 0, 0, 0, tzinfo=UTC)) == date(2026, 10, 23)

    def test_positive_offset_pushes_late_evening_into_next_day(self):
        ts = datetime(2026, 10, 22, 21, 30, tzinfo=UTC)
        assert local_day(ts, 180) == date(2026, 10, 23)  # 00:30 in UTC+3
        assert local_day(ts, 149) == date(2026, 10, 22)  # 23:59 local, still the 22nd
        assert local_day(ts, 150) == date(2026, 10, 23)  # 00:00 local

    def test_exact_local_midnight_belongs_to_the_new_day(self):
        assert local_day(datetime(2026, 10, 22, 21, 0, tzinfo=UTC), 180) == date(2026, 10, 23)
        assert local_day(datetime(2026, 10, 22, 20, 59, 59, tzinfo=UTC), 180) == date(2026, 10, 22)

    def test_negative_offset(self):
        ts = datetime(2026, 10, 22, 2, 0, tzinfo=UTC)
        assert local_day(ts, -300) == date(2026, 10, 21)  # 21:00 the evening before in UTC-5

    def test_extreme_offsets(self):
        ts = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
        assert local_day(ts, 14 * 60) == date(2026, 10, 23)
        assert local_day(ts, -14 * 60) == date(2026, 10, 21)

    def test_naive_timestamps_are_utc(self):
        assert local_day(datetime(2026, 10, 22, 23, 0), 120) == date(2026, 10, 23)

    def test_aware_timestamp_in_another_zone_is_converted(self):
        plus5 = timezone(timedelta(hours=5))
        ts = datetime(2026, 10, 23, 2, 0, tzinfo=plus5)  # = 21:00 UTC on the 22nd
        assert local_day(ts, 0) == date(2026, 10, 22)


class TestComputeStreak:
    def test_no_activity(self):
        r = compute_streak([], NOW)
        assert (r.current, r.longest, r.last_active, r.active_today, r.days) == (
            0, 0, None, False, [],
        )

    def test_single_visit_today(self):
        r = compute_streak([at(0)], NOW)
        assert r.current == 1 and r.longest == 1
        assert r.active_today is True
        assert r.last_active == date(2026, 10, 22)
        assert r.days == [date(2026, 10, 22)]

    def test_streak_alive_when_last_active_is_yesterday(self):
        r = compute_streak([at(1), at(2), at(3)], NOW)
        assert r.current == 3
        assert r.active_today is False
        assert r.last_active == date(2026, 10, 21)

    def test_streak_dead_when_last_active_two_days_ago(self):
        r = compute_streak([at(2), at(3), at(4)], NOW)
        assert r.current == 0
        assert r.longest == 3  # history is kept
        assert r.last_active == date(2026, 10, 20)

    def test_gap_resets_current_but_not_longest(self):
        r = compute_streak([at(0), at(1), at(5), at(6), at(7), at(8)], NOW)
        assert r.current == 2
        assert r.longest == 4

    def test_longest_can_be_the_current_one(self):
        r = compute_streak([at(0), at(1), at(2), at(3), at(10), at(11)], NOW)
        assert r.current == 4 and r.longest == 4

    def test_duplicates_on_one_day_count_once(self):
        r = compute_streak([at(0, 8), at(0, 9), at(0, 23, 59), at(1, 0, 1)], NOW)
        assert r.current == 2 and r.longest == 2
        assert len(r.days) == 2

    def test_unsorted_input(self):
        r = compute_streak([at(3), at(0), at(2), at(1)], NOW)
        assert r.current == 4

    def test_future_timestamps_are_ignored(self):
        r = compute_streak([at(0), NOW + timedelta(days=2), NOW + timedelta(hours=30)], NOW)
        assert r.current == 1 and r.longest == 1
        assert r.last_active == date(2026, 10, 22)
        assert max(r.days) == date(2026, 10, 22)

    def test_only_future_timestamps(self):
        r = compute_streak([NOW + timedelta(days=3)], NOW)
        assert r.last_active is None and r.current == 0 and r.days == []

    def test_midnight_boundary_utc(self):
        # 23:59 on the 21st and 00:00 on the 22nd are consecutive days, one minute apart
        r = compute_streak([at(1, 23, 59), at(0, 0, 0)], NOW)
        assert r.current == 2

    def test_same_instants_can_be_one_day_for_one_viewer_and_two_for_another(self):
        stamps = [
            datetime(2026, 10, 21, 22, 0, tzinfo=UTC),
            datetime(2026, 10, 22, 1, 0, tzinfo=UTC),
        ]
        assert compute_streak(stamps, NOW, 0).current == 2
        east = compute_streak(stamps, NOW, 180)  # both land on the 22nd (01:00 and 04:00)
        assert east.days == [date(2026, 10, 22)]
        assert east.current == 1 and east.longest == 1

    def test_offset_moves_today_too(self):
        # At 22:30 UTC on the 22nd it is already the 23rd in UTC+3: a visit made earlier
        # "today" in UTC is yesterday's activity for that viewer, but the streak is alive.
        now = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)
        visit = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
        r = compute_streak([visit], now, 180)
        assert r.active_today is False
        assert r.current == 1
        assert r.last_active == date(2026, 10, 22)

    def test_negative_offset_viewer_still_on_previous_day(self):
        now = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)  # 22:00 on the 21st in UTC-5
        visit = datetime(2026, 10, 22, 2, 0, tzinfo=UTC)  # 21:00 on the 21st local
        r = compute_streak([visit], now, -300)
        assert r.active_today is True
        assert r.last_active == date(2026, 10, 21)

    def test_days_window_is_last_60_days_ascending(self):
        stamps = [at(0), at(59), at(60), at(100)]
        r = compute_streak(stamps, NOW)
        assert r.days == [date(2026, 10, 22) - timedelta(days=59), date(2026, 10, 22)]
        assert r.days == sorted(r.days)
        assert STREAK_WINDOW_DAYS == 60
        assert r.longest == 2  # day 59 and day 60 are consecutive, even if 60 is off the strip

    def test_streak_longer_than_window_counts_in_full(self):
        stamps = [at(i) for i in range(90)]
        r = compute_streak(stamps, NOW)
        assert r.current == 90 and r.longest == 90
        assert len(r.days) == 60

    def test_absurd_timestamps_do_not_crash(self):
        r = compute_streak([datetime.min.replace(tzinfo=UTC), at(0)], NOW, -14 * 60)
        assert r.current == 1


def _visit(ts: datetime, status: str = "visited", pid: str = "p") -> Visit:
    return Visit(
        id=f"v_{ts.isoformat()}_{pid}", place_id=pid, status=status, visited_at=ts,
        coordinates=Coordinates(lat=42.0, lng=18.0),
    )


class TestStreakEndpoint:
    def test_empty_journal(self, fresh_journal):
        resp = client.get("/api/streak")
        assert resp.status_code == 200
        assert resp.json() == {
            "current": 0, "longest": 0, "last_active": None, "active_today": False, "days": [],
        }

    def test_contract_shape_with_history(self):
        visits = [_visit(datetime.now(UTC) - timedelta(days=d), pid=f"p{d}") for d in (0, 1, 2, 9)]
        with patch(
            "app.services.streak.journal.list_visits",
            new_callable=AsyncMock, return_value=(visits, len(visits)),
        ):
            data = client.get("/api/streak").json()
        assert set(data) == {"current", "longest", "last_active", "active_today", "days"}
        assert data["current"] == 3 and data["longest"] == 3
        assert data["active_today"] is True
        assert data["last_active"] == datetime.now(UTC).date().isoformat()
        assert len(data["days"]) == 4 and data["days"] == sorted(data["days"])
        assert all(len(d) == 10 and d[4] == "-" for d in data["days"])

    def test_tz_offset_is_applied(self):
        # 23:30 UTC yesterday-or-today is the next calendar day for a UTC+3 viewer
        ts = datetime.now(UTC).replace(hour=22, minute=30, second=0, microsecond=0)
        if ts > datetime.now(UTC):
            ts -= timedelta(days=1)
        visits = [_visit(ts)]
        with patch(
            "app.services.streak.journal.list_visits",
            new_callable=AsyncMock, return_value=(visits, 1),
        ):
            utc = client.get("/api/streak?tz_offset_minutes=0").json()
            east = client.get("/api/streak?tz_offset_minutes=180").json()
        assert date.fromisoformat(east["last_active"]) == date.fromisoformat(utc["last_active"]) + timedelta(days=1)

    @pytest.mark.parametrize("offset", [841, -841, 100000])
    def test_offset_out_of_range_is_422(self, offset):
        assert client.get(f"/api/streak?tz_offset_minutes={offset}").status_code == 422

    @pytest.mark.parametrize("offset", [840, -840, 0])
    def test_offset_limits_are_accepted(self, offset, fresh_journal):
        assert client.get(f"/api/streak?tz_offset_minutes={offset}").status_code == 200

    def test_non_integer_offset_is_422(self):
        assert client.get("/api/streak?tz_offset_minutes=abc").status_code == 422

    def test_real_journal_counts_only_visited_status(self, fresh_journal):
        client.post("/api/visits", json={"place_id": "w", "lat": 1, "lng": 1, "status": "want_to_visit"})
        client.post("/api/visits", json={"place_id": "s", "lat": 1, "lng": 1, "status": "skip"})
        assert client.get("/api/streak").json()["current"] == 0
        client.post("/api/visits", json={"place_id": "v", "lat": 1, "lng": 1, "status": "visited"})
        data = client.get("/api/streak").json()
        assert data["current"] == 1 and data["active_today"] is True

    def test_journal_failure_is_500_without_details(self):
        with patch(
            "app.services.streak.journal.list_visits",
            new_callable=AsyncMock, side_effect=RuntimeError("secret path /etc/x"),
        ):
            resp = client.get("/api/streak")
        assert resp.status_code == 500
        assert "secret" not in resp.text
