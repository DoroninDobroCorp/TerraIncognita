"""Regressions found by the third review pass: stores, journal/gamification/community rules, encoding."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

from app.main import GeoDataFiles, SafeJSONResponse, app
from app.models.journal import Visit, VisitStatus
from app.models.place import Coordinates
from app.services import gamification as gam
from app.services import journal
from app.services.community import KARMA_REVIEW_WRITTEN, CommunityService
from app.utils.atomic_io import atomic_write_text
from app.utils.sanitize import sanitize_user_input

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _fresh():
    journal._reset_store()
    gam._reset_store()
    yield
    journal._reset_store()
    gam._reset_store()


def _visit_json(vid: str) -> dict:
    return Visit(
        id=vid, place_id=f"p_{vid}", coordinates=Coordinates(lat=1.0, lng=2.0),
        visited_at=datetime(2026, 1, 1, tzinfo=UTC),
    ).model_dump(mode="json")


# ── a store that failed to load stays "not loaded" (the next save must not write over unread files) ──


class TestStoresLoadAllOrNothing:
    async def test_journal_retries_the_whole_load_after_a_failure(self, monkeypatch):
        data_dir = journal._get_data_dir()
        (data_dir / "visits.json").write_text(json.dumps({"v1": _visit_json("v1")}), encoding="utf-8")
        real = journal.load_model_records

        def flaky(path, model):
            if path.name == "notes.json":
                raise RuntimeError("disk hiccup")
            return real(path, model)

        monkeypatch.setattr(journal, "load_model_records", flaky)
        with pytest.raises(RuntimeError):
            await journal.list_visits()
        assert journal._loaded is False and not journal._visits  # nothing half-loaded to be saved over the files

        monkeypatch.setattr(journal, "load_model_records", real)
        visits, total = await journal.list_visits()
        assert total == 1 and visits[0].id == "v1"

    async def test_gamification_retries_the_whole_load_after_a_failure(self, monkeypatch):
        data_dir = gam._get_data_dir()
        (data_dir / "xp_events.json").write_text(json.dumps({"total_xp": 120, "events": [], "refs": ["visit:p"]}))
        real = gam.load_model_records

        def flaky(path, model):
            if path.name == "achievements.json":
                raise RuntimeError("disk hiccup")
            return real(path, model)

        monkeypatch.setattr(gam, "load_model_records", flaky)
        with pytest.raises(RuntimeError):
            await gam._award_xp(10, "t", "t")
        assert gam._loaded is False and gam._total_xp == 0

        monkeypatch.setattr(gam, "load_model_records", real)
        assert await gam._award_xp(10, "t", "t") is True
        assert gam._total_xp == 130  # the 120 XP on disk were not lost

    async def test_community_retries_the_whole_load_after_a_failure(self, tmp_path, monkeypatch):
        svc = CommunityService(data_dir=str(tmp_path))
        await svc.submit_place(name="Spot", description="", categories=[], lat=1, lng=1, photos=[], tags=[],
                               author_id="alice")
        svc = CommunityService(data_dir=str(tmp_path))  # a restart
        real = svc._load_store

        def flaky(filename, model):
            if filename == "karma.json":
                raise RuntimeError("disk hiccup")
            return real(filename, model)

        monkeypatch.setattr(svc, "_load_store", flaky)
        with pytest.raises(RuntimeError):
            svc._ensure_loaded()
        assert svc._loaded is False and not svc._places
        monkeypatch.setattr(svc, "_load_store", real)
        svc._ensure_loaded()
        assert len(svc._places) == 1


class TestPartlyDamagedStoreKeepsItsOriginal:
    """Valid records are used, the damaged ones are preserved — and the file stays until the next save,
    so a restart before then still finds the good records."""

    async def test_journal_file_survives_next_to_its_backup(self):
        data_dir = journal._get_data_dir()
        raw = {"bad": {"id": "bad"}, "good": _visit_json("good")}
        (data_dir / "visits.json").write_text(json.dumps(raw), encoding="utf-8")
        await journal.list_visits()
        assert json.loads((data_dir / "visits.json").read_text()).keys() == raw.keys()
        assert len(list(data_dir.glob("visits.json.corrupt-*"))) == 1

    async def test_an_unparseable_file_is_still_moved_aside(self):
        data_dir = journal._get_data_dir()
        (data_dir / "visits.json").write_text("{not json", encoding="utf-8")
        await journal.list_visits()
        assert not (data_dir / "visits.json").exists()
        assert len(list(data_dir.glob("visits.json.corrupt-*"))) == 1

    async def test_community_file_survives_next_to_its_backup(self, tmp_path):
        good = CommunityService(data_dir=str(tmp_path / "seed"))
        place = await good.submit_place(name="Spot", description="", categories=[], lat=1, lng=1, photos=[],
                                        tags=[], author_id="alice")
        stored = json.loads((tmp_path / "seed" / "places.json").read_text())
        (tmp_path / "places.json").write_text(json.dumps({**stored, "bad": {"id": "bad"}}), encoding="utf-8")
        svc = CommunityService(data_dir=str(tmp_path))
        svc._ensure_loaded()
        assert list(svc._places) == [place.id]
        assert "bad" in json.loads((tmp_path / "places.json").read_text())
        assert len(list(tmp_path.glob("places.json.corrupt-*"))) == 1


# ── lone surrogates: valid in a Python str, impossible in UTF-8 ──


class TestSurrogates:
    def test_user_input_loses_them(self):
        assert sanitize_user_input("a\ud800b\udfffc") == "abc"

    def test_a_json_response_still_encodes(self):
        body = SafeJSONResponse({"name": "x\ud800y"}).body
        assert json.loads(body)["name"].startswith("x") and body.endswith(b"y\"}")

    def test_a_save_does_not_fail_half_way(self, tmp_path):
        atomic_write_text(tmp_path / "f.json", json.dumps({"name": "x\ud800y"}, ensure_ascii=False))
        assert (tmp_path / "f.json").read_text(encoding="utf-8").startswith('{"name": "x')

    def test_an_api_request_carrying_one_is_not_a_500(self):
        body = b'{"place_id": "p1", "lat": 42.4, "lng": 18.5, "place_name": "Fort \\ud800 Mamula"}'
        resp = client.post("/api/visits", content=body, headers={"content-type": "application/json"})
        assert resp.status_code in (200, 422), resp.text  # validation may refuse it; it must not crash
        if resp.status_code == 200:
            assert "\ud800" not in resp.json()["place_name"]


# ── /data serves geodata only ──


class TestGeoDataFiles:
    @pytest.fixture
    def web(self, tmp_path):
        (tmp_path / "area.geojson").write_text('{"type": "FeatureCollection", "features": []}')
        (tmp_path / "AREA2.GEOJSON").write_text("{}")
        (tmp_path / "visits.json").write_text("{}")
        (tmp_path / "journal").mkdir()
        (tmp_path / "journal" / "visits.json").write_text("{}")
        (tmp_path / "journal" / "map.geojson").write_text("{}")
        return TestClient(Starlette(routes=[Mount("/data", GeoDataFiles(directory=str(tmp_path)))]))

    def test_geojson_is_served(self, web):
        assert web.get("/data/area.geojson").status_code == 200
        assert web.get("/data/AREA2.GEOJSON").status_code == 200

    @pytest.mark.parametrize("path", ["visits.json", "journal/visits.json", "journal/", "", "nope.geojson.bak", "../x.geojson"])
    def test_everything_else_is_a_404(self, web, path):
        assert web.get(f"/data/{path}").status_code == 404

    def test_the_real_mount_does_not_serve_the_journal(self):
        assert client.get("/data/journal/visits.json").status_code == 404
        assert client.get("/data/visits.json").status_code == 404


# ── journal ──


class TestJournalRules:
    async def test_marking_a_planned_place_visited_pays_the_visit_xp_once(self):
        planned = await journal.create_visit("p1", 42.4, 18.5, status=VisitStatus.WANT_TO_VISIT)
        assert gam._total_xp == 0
        await journal.update_visit(planned.id, status=VisitStatus.VISITED)
        earned = gam._total_xp
        assert earned > 0
        await journal.update_visit(planned.id, status=VisitStatus.VISITED)  # no change of status: nothing new
        await journal.update_visit(planned.id, duration_minutes=30)
        assert gam._total_xp == earned

    async def test_checking_in_next_to_a_planned_and_a_visited_record_reports_the_visited_one(self):
        await journal.create_visit("planned", 42.4, 18.5, status=VisitStatus.WANT_TO_VISIT)  # stored first
        await journal.create_visit("done", 42.4, 18.5, status=VisitStatus.VISITED)
        result = await journal.check_proximity(42.4, 18.5)
        assert (result.nearby_place_id, result.already_visited) == ("done", True)

    async def test_note_bonuses_are_paid_once_per_place_however_often_notes_are_rewritten(self):
        visit = await journal.create_visit("p1", 42.4, 18.5)
        before = gam._total_xp
        first = await journal.create_note(visit.id, text="first")
        after_first = gam._total_xp
        assert after_first > before
        await journal.delete_note(first.id)
        await journal.create_note(visit.id, text="second")
        again = await journal.create_visit("p1", 42.4, 18.5)  # another check-in at the same place
        await journal.create_note(again.id, text="third")
        assert gam._total_xp == after_first

    async def test_a_different_place_pays_its_own_note_bonus(self):
        a = await journal.create_visit("p1", 42.4, 18.5)
        await journal.create_note(a.id, text="a")
        total = gam._total_xp
        b = await journal.create_visit("p2", 42.5, 18.6)
        await journal.create_note(b.id, text="b")
        assert gam._total_xp > total

    def test_a_date_without_a_utc_equivalent_is_a_422(self):
        resp = client.post("/api/trips", json={"name": "t", "start_date": "0001-01-01T00:00:00+05:00"})
        assert resp.status_code == 422

    async def test_regrouping_moves_visits_between_trips_without_duplicates(self):
        a = await journal.create_trip("a")
        b = await journal.create_trip("b")
        visits = [await journal.create_visit(f"p{i}", 42.4, 18.5) for i in range(4)]
        start, end = datetime.now(UTC) - timedelta(days=1), datetime.now(UTC) + timedelta(days=1)
        await journal.auto_group_visits(a.id, start, end)
        assert sorted(journal._trips[a.id].visit_ids) == sorted(v.id for v in visits)
        await journal.auto_group_visits(b.id, start, end)
        await journal.auto_group_visits(b.id, start, end)  # repeating it changes nothing
        assert journal._trips[a.id].visit_ids == []
        assert sorted(journal._trips[b.id].visit_ids) == sorted(v.id for v in visits)
        assert all(v.trip_id == b.id for v in visits)

    async def test_grouping_a_large_journal_is_not_quadratic(self):
        journal._ensure_loaded()
        now = datetime.now(UTC)
        for i in range(15_000):
            journal._visits[f"v{i}"] = Visit(
                id=f"v{i}", place_id=f"p{i}", coordinates=Coordinates(lat=42.0, lng=18.0), visited_at=now,
            )
        trip = await journal.create_trip("big")
        t0 = time.process_time()
        await journal.auto_group_visits(trip.id, now - timedelta(days=1), now + timedelta(days=1))
        assert time.process_time() - t0 < 1.0  # the list scan took ~2 s here, and 56 s at the 50k cap
        assert len(journal._trips[trip.id].visit_ids) == 15_000


class TestFogCap:
    async def test_the_response_says_when_the_store_is_full(self, monkeypatch):
        monkeypatch.setattr(gam, "MAX_FOG_CELLS", 5)
        points = [(42.4 + i * 0.01, 18.5, None) for i in range(3)]
        full = await gam.reveal_fog(points, radius_m=500)
        assert full.capped is True
        assert full.total_explored_cells <= 5

    async def test_it_does_not_when_there_is_room(self):
        result = await gam.reveal_fog([(42.4, 18.5, None)], radius_m=100)
        assert result.capped is False and result.new_cells_revealed > 0


# ── community ──


class TestCommunityRules:
    @pytest.fixture
    def svc(self, tmp_path):
        return CommunityService(data_dir=str(tmp_path))

    async def test_a_name_that_is_only_invisible_characters_is_refused(self, svc):
        with pytest.raises(ValueError, match="name"):
            await svc.submit_place(name="​​", description="", categories=[], lat=1, lng=1, photos=[],
                                   tags=[], author_id="alice")

    async def test_a_review_without_text_is_refused_but_a_tip_may_be_empty(self, svc):
        from app.models.community import ReviewType

        place = await svc.submit_place(name="Spot", description="", categories=[], lat=1, lng=1, photos=[],
                                       tags=[], author_id="alice")
        with pytest.raises(ValueError, match="Review text"):
            await svc.create_review(place.id, "bob", "​")
        tip = await svc.create_review(place.id, "bob", "", review_type=ReviewType.TIP)
        assert tip.review_type == ReviewType.TIP

    async def test_deleting_a_place_takes_back_its_reviewers_karma_too(self, svc):
        place = await svc.submit_place(name="Spot", description="", categories=[], lat=1, lng=1, photos=[],
                                       tags=[], author_id="alice")
        await svc.create_review(place.id, "bob", "Lovely")
        assert (await svc.get_karma("bob")).karma == KARMA_REVIEW_WRITTEN
        await svc.delete_place(place.id, "alice")
        bob = await svc.get_karma("bob")
        assert bob.karma == 0 and bob.reviews_written == 0
        assert (await svc.get_karma("alice")).karma == 0


# ── chat ──


class TestChatMessageThatSanitisesToNothing:
    def test_it_is_a_400_and_never_reaches_the_provider(self, monkeypatch):
        from unittest.mock import AsyncMock

        provider = AsyncMock(return_value="{}")
        monkeypatch.setattr("app.services.llm_discovery.chat_completion", provider)
        resp = client.post("/api/chat", json={"message": "<system>", "lat": 42.4, "lng": 18.5})
        assert resp.status_code == 400
        provider.assert_not_called()
