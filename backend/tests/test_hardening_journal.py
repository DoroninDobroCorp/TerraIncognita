"""Journal hardening: naive datetimes, trip integrity, proximity, unicode tags, exports, caps."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import gamification, journal

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _fresh():
    journal._reset_store()
    gamification._reset_store()
    yield
    journal._reset_store()
    gamification._reset_store()


def _visit(place="p", lat=42.4, lng=18.7, status="visited", **extra) -> dict:
    body = {"place_id": place, "lat": lat, "lng": lng, "status": status, **extra}
    r = client.post("/api/visits", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _trip(name="T", **extra) -> dict:
    r = client.post("/api/trips", json={"name": name, **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ── datetimes ────────────────────────────────────────────────────


class TestNaiveDatetimes:
    def test_auto_group_accepts_naive_dates(self):
        """naive vs aware comparison raised TypeError -> 500."""
        trip = _trip()
        visit = _visit()
        r = client.post(f"/api/trips/{trip['id']}/auto-group", json={
            "start_date": "2020-01-01T00:00:00", "end_date": "2099-01-01T00:00:00"})
        assert r.status_code == 200
        assert visit["id"] in r.json()["visit_ids"]

    def test_trip_dates_are_stored_as_utc(self):
        trip = _trip(start_date="2026-05-01T10:00:00", end_date="2026-05-02T10:00:00+02:00")
        assert trip["start_date"].endswith(("Z", "+00:00"))
        assert trip["end_date"].startswith("2026-05-02T08:00:00")

    def test_auto_group_with_end_before_start_is_rejected(self):
        trip = _trip()
        r = client.post(f"/api/trips/{trip['id']}/auto-group", json={
            "start_date": "2026-05-02T00:00:00Z", "end_date": "2026-05-01T00:00:00Z"})
        assert r.status_code == 422

    async def test_service_accepts_naive_datetimes_from_direct_callers(self):
        from datetime import datetime

        trip = await journal.create_trip("T")
        await journal.create_visit("p", 1.0, 2.0)
        result = await journal.auto_group_visits(trip.id, datetime(2020, 1, 1), datetime(2099, 1, 1))
        assert result is not None and len(result.visit_ids) == 1


# ── trip integrity ───────────────────────────────────────────────


class TestTripMembership:
    def test_auto_group_moves_a_visit_out_of_its_previous_trip(self):
        a, b = _trip("A"), _trip("B")
        visit = _visit(trip_id=a["id"])
        assert client.get(f"/api/trips/{a['id']}").json()["visit_ids"] == [visit["id"]]

        client.post(f"/api/trips/{b['id']}/auto-group", json={
            "start_date": "2020-01-01T00:00:00Z", "end_date": "2099-01-01T00:00:00Z"})

        assert client.get(f"/api/trips/{b['id']}").json()["visit_ids"] == [visit["id"]]
        assert client.get(f"/api/trips/{a['id']}").json()["visit_ids"] == []
        assert client.get(f"/api/visits/{visit['id']}").json()["trip_id"] == b["id"]

    def test_check_in_with_unknown_trip_is_404_and_stores_nothing(self):
        r = client.post("/api/visits", json={"place_id": "p", "lat": 1, "lng": 2, "trip_id": "trip_nope"})
        assert r.status_code == 404
        assert client.get("/api/visits").json()["total"] == 0

    def test_moving_a_visit_to_an_unknown_trip_is_404_and_leaves_it_in_place(self):
        a = _trip("A")
        visit = _visit(trip_id=a["id"])
        r = client.patch(f"/api/visits/{visit['id']}", json={"trip_id": "trip_nope"})
        assert r.status_code == 404
        assert client.get(f"/api/trips/{a['id']}").json()["visit_ids"] == [visit["id"]]
        assert client.get(f"/api/visits/{visit['id']}").json()["trip_id"] == a["id"]

    def test_empty_trip_id_detaches_the_visit(self):
        a = _trip("A")
        visit = _visit(trip_id=a["id"])
        r = client.patch(f"/api/visits/{visit['id']}", json={"trip_id": ""})
        assert r.status_code == 200
        assert r.json()["trip_id"] is None
        assert client.get(f"/api/trips/{a['id']}").json()["visit_ids"] == []


# ── proximity ────────────────────────────────────────────────────


class TestProximity:
    def test_returns_the_nearest_place_not_the_first_one_stored(self):
        _visit("far_wish", lat=42.4000, lng=18.7005, status="want_to_visit")   # ~41 m
        _visit("here", lat=42.4000, lng=18.70012, status="visited")            # ~10 m
        r = client.post("/api/visits/proximity", json={"lat": 42.4, "lng": 18.7, "radius_m": 100})
        body = r.json()
        assert body["nearby_place_id"] == "here"
        assert body["already_visited"] is True
        assert body["distance_m"] < 15


# ── tags / text ──────────────────────────────────────────────────


class TestTags:
    def test_non_ascii_letters_survive_tag_sanitising(self):
        """The whitelist was [a-zA-Zа-яА-Я0-9_-]: 'šuma' became 'uma', 'tvrđava' 'tvrava'."""
        visit = _visit()
        r = client.post(f"/api/visits/{visit['id']}/notes", json={
            "text": "x", "tags": ["Šuma", "tvrđava", "ćuprija", "заброшка", "<b>bold</b>", "a b"]})
        assert r.status_code == 200
        assert r.json()["tags"] == ["šuma", "tvrđava", "ćuprija", "заброшка", "bboldb", "ab"]

    def test_overlong_note_fields_are_rejected_not_silently_cut(self):
        visit = _visit()
        r = client.post(f"/api/visits/{visit['id']}/notes", json={"text": "x" * 10_001})
        assert r.status_code == 422
        r = client.post(f"/api/visits/{visit['id']}/notes", json={"tags": ["x" * 101]})
        assert r.status_code == 422


# ── validation / paging ──────────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize("duration", [-5, 10**9])
    def test_duration_must_be_sane(self, duration):
        r = client.post("/api/visits", json={"place_id": "p", "lat": 1, "lng": 2, "duration_minutes": duration})
        assert r.status_code == 422

    def test_place_id_must_be_non_empty_and_bounded(self):
        assert client.post("/api/visits", json={"place_id": "", "lat": 1, "lng": 2}).status_code == 422
        assert client.post("/api/visits", json={"place_id": "x" * 500, "lat": 1, "lng": 2}).status_code == 422

    @pytest.mark.parametrize("path", ["/api/visits", "/api/trips"])
    @pytest.mark.parametrize("qs", ["limit=0", "limit=-1", "limit=100000", "offset=-1"])
    def test_paging_bounds(self, path, qs):
        assert client.get(f"{path}?{qs}").status_code == 422

    def test_trip_name_cannot_inject_lines(self):
        trip = _trip("Summer\n# Injected\r\nheading")
        assert "\n" not in trip["name"] and "\r" not in trip["name"]


# ── exports ──────────────────────────────────────────────────────


class TestExports:
    def _export(self, trip_id, fmt):
        r = client.post(f"/api/trips/{trip_id}/export", json={"format": fmt})
        assert r.status_code == 200
        return r.json()

    def test_filename_is_a_plain_safe_name(self):
        trip = _trip('../../etc/pass wd"; rm -rf \\ <x>')
        for fmt, ext in (("markdown", "md"), ("json", "json"), ("html", "html")):
            name = self._export(trip["id"], fmt)["filename"]
            assert re.fullmatch(r"[\w.\-]+", name), name
            assert not name.startswith(".")
            assert ".." not in name
            assert name.endswith(f".{ext}")

    def test_html_export_escapes_everything_user_controlled(self):
        trip = _trip("<script>alert(1)</script>", region='"><img src=x onerror=alert(2)>')
        visit = _visit(place="p<1>", trip_id=trip["id"])
        client.post(f"/api/visits/{visit['id']}/notes", json={
            "text": "<img src=x onerror=alert(3)>", "rating": {"atmosphere": 5}})
        html = self._export(trip["id"], "html")["content"]
        assert "<script>" not in html
        assert "<img" not in html
        assert "&lt;img src=x onerror=alert(3)&gt;" in html
        assert "p&lt;1&gt;" in html

    def test_markdown_export_keeps_note_text_inside_its_blockquote(self):
        trip = _trip("T")
        visit = _visit(trip_id=trip["id"])
        client.post(f"/api/visits/{visit['id']}/notes", json={
            "text": "first\n# Injected heading\n- injected item\n[click](javascript:alert(1))"})
        md = self._export(trip["id"], "markdown")["content"]
        for line in md.splitlines():
            if "Injected" in line or "injected" in line or "click" in line:
                assert line.lstrip().startswith(">"), f"note text escaped its quote: {line!r}"
        assert "[click](" not in md


# ── growth caps ──────────────────────────────────────────────────


class TestCaps:
    def test_visit_count_is_capped(self, monkeypatch):
        monkeypatch.setattr(journal, "MAX_VISITS", 3)
        for i in range(3):
            _visit(f"p{i}")
        r = client.post("/api/visits", json={"place_id": "p9", "lat": 1, "lng": 2})
        assert r.status_code == 507

    def test_notes_per_visit_are_capped(self, monkeypatch):
        monkeypatch.setattr(journal, "MAX_NOTES_PER_VISIT", 2)
        visit = _visit()
        for _ in range(2):
            assert client.post(f"/api/visits/{visit['id']}/notes", json={"text": "x"}).status_code == 200
        assert client.post(f"/api/visits/{visit['id']}/notes", json={"text": "x"}).status_code == 507

    def test_trip_count_is_capped(self, monkeypatch):
        monkeypatch.setattr(journal, "MAX_TRIPS", 2)
        _trip("a")
        _trip("b")
        assert client.post("/api/trips", json={"name": "c"}).status_code == 507
