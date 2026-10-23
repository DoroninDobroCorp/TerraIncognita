"""Regressions found by the fourth review pass: stores, journal rules, community edits, offline cache."""

from __future__ import annotations

import errno
import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.community import ReviewType
from app.models.journal import PlaceNote, PlaceRating, Trip, Visit, VisitStatus
from app.models.place import Coordinates, DiscoverResponse, Place, PlaceSource
from app.services import gamification as gam
from app.services import journal
from app.services.community import CommunityService
from app.services.offline_manager import offline_manager
from app.utils import atomic_io
from app.utils.atomic_io import atomic_write_text, load_model_records

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _fresh():
    journal._reset_store()
    gam._reset_store()
    offline_manager._reset_store()
    yield
    journal._reset_store()
    gam._reset_store()
    offline_manager._reset_store()


def _visit(vid: str, place: str | None = None, status=VisitStatus.VISITED, trip: str | None = None, **kw) -> Visit:
    return Visit(
        id=vid, place_id=place or f"p_{vid}", coordinates=Coordinates(lat=42.4, lng=18.5), status=status,
        visited_at=kw.pop("visited_at", datetime.now(UTC)), trip_id=trip, **kw,
    )


# ── GET /api/stats must not be quadratic ──


def test_surprises_for_thousands_of_rated_places_take_a_blink():
    journal._ensure_loaded()
    for i in range(6000):
        journal._visits[f"v{i}"] = _visit(f"v{i}", f"p{i}")
        journal._notes[f"n{i}"] = PlaceNote(
            id=f"n{i}", visit_id=f"v{i}", place_id=f"p{i}", rating=PlaceRating(atmosphere=5, uniqueness=5),
        )
    t0 = time.process_time()
    surprises = journal._compute_surprises()
    assert time.process_time() - t0 < 1.0  # a scan per place took ~3 s here and a minute at 20k
    assert len(surprises) == 5 and all(s.place_name is None or s.place_id for s in surprises)


# ── one backup per distinct damaged content ──


class TestBackupsOfPartlyDamagedFiles:
    def _damaged(self, tmp_path, extra=0):
        raw = {"good": _visit("good").model_dump(mode="json"), "bad": {"id": "bad"}}
        raw.update({f"bad{i}": {"id": f"bad{i}"} for i in range(extra)})
        path = tmp_path / "visits.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def test_loading_again_does_not_copy_again(self, tmp_path):
        path = self._damaged(tmp_path)
        for _ in range(5):  # five starts, or five retries of a load that failed on a later file
            assert set(load_model_records(path, Visit)) == {"good"}
        assert len(list(tmp_path.glob("visits.json.corrupt-*"))) == 1
        assert path.exists()  # the original stays until the next save

    def test_other_damaged_content_gets_its_own_backup(self, tmp_path):
        path = self._damaged(tmp_path)
        load_model_records(path, Visit)
        self._damaged(tmp_path, extra=1)
        load_model_records(path, Visit)
        assert len(list(tmp_path.glob("visits.json.corrupt-*"))) == 2

    def test_an_unparseable_file_is_still_moved_not_copied(self, tmp_path):
        path = tmp_path / "visits.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_model_records(path, Visit) == {}
        assert not path.exists() and len(list(tmp_path.glob("visits.json.corrupt-*"))) == 1


# ── fsync failures are not "unsupported fsync" ──


class TestAtomicWriteFsync:
    def test_a_real_io_error_keeps_the_old_copy(self, tmp_path):
        target = tmp_path / "a.json"
        atomic_write_text(target, "old")
        with patch.object(atomic_io.os, "fsync", side_effect=OSError(errno.EIO, "I/O error")), pytest.raises(OSError):
            atomic_write_text(target, "new")
        assert target.read_text() == "old"
        assert [p.name for p in tmp_path.iterdir()] == ["a.json"]  # and no temp file is left behind

    @pytest.mark.parametrize("code", [errno.EINVAL, errno.ENOTSUP])
    def test_a_filesystem_without_fsync_still_works(self, tmp_path, code):
        target = tmp_path / "a.json"
        with patch.object(atomic_io.os, "fsync", side_effect=OSError(code, "unsupported")):
            atomic_write_text(target, "new")
        assert target.read_text() == "new"


# ── achievements need places that were visited ──


class TestAchievementsCountVisitedPlacesOnly:
    async def test_notes_on_planned_places_unlock_nothing(self):
        visits, notes = [], []
        for i in range(3):
            visits.append(_visit(f"plan{i}", status=VisitStatus.WANT_TO_VISIT))
            notes.append(PlaceNote(id=f"n{i}", visit_id=f"plan{i}", place_id=f"p_plan{i}", tags=["cave"], photos=["x.jpg"]))
        visits.append(_visit("real"))
        result = await gam.check_achievements(visits, notes)
        assert "cave_3" not in {a.definition.id for a in result.new_achievements}

    async def test_the_same_notes_on_visited_places_do(self):
        visits = [_visit(f"v{i}") for i in range(3)]
        notes = [PlaceNote(id=f"n{i}", visit_id=f"v{i}", place_id=f"p_v{i}", tags=["cave"]) for i in range(3)]
        result = await gam.check_achievements(visits, notes)
        assert "cave_3" in {a.definition.id for a in result.new_achievements}


# ── trips written by 0.1.0 listed a visit in two trips ──


class TestLegacyTripLinks:
    def test_each_visit_is_listed_only_in_the_trip_it_names(self):
        data_dir = journal._get_data_dir()
        visits = {v.id: v.model_dump(mode="json") for v in (_visit("v1", trip="B"), _visit("v2", trip="B"), _visit("v3"))}
        trips = {
            "A": Trip(id="A", name="A", visit_ids=["v1", "v2", "gone"]).model_dump(mode="json"),  # old list, moved visits, deleted id
            "B": Trip(id="B", name="B", visit_ids=["v1", "v2", "v1"]).model_dump(mode="json"),
        }
        (data_dir / "visits.json").write_text(json.dumps(visits), encoding="utf-8")
        (data_dir / "trips.json").write_text(json.dumps(trips), encoding="utf-8")
        journal._ensure_loaded()
        assert journal._trips["A"].visit_ids == []
        assert journal._trips["B"].visit_ids == ["v1", "v2"]

    async def test_the_summary_of_the_old_trip_stops_counting_moved_visits(self):
        data_dir = journal._get_data_dir()
        (data_dir / "visits.json").write_text(
            json.dumps({"v1": _visit("v1", trip="B").model_dump(mode="json")}), encoding="utf-8")
        (data_dir / "trips.json").write_text(json.dumps({
            "A": Trip(id="A", name="A", visit_ids=["v1"]).model_dump(mode="json"),
            "B": Trip(id="B", name="B", visit_ids=["v1"]).model_dump(mode="json"),
        }), encoding="utf-8")
        assert (await journal.get_trip_summary("A")).total_places == 0
        assert (await journal.get_trip_summary("B")).total_places == 1


# ── a planned visit that becomes a real one happened now ──


async def test_marking_a_planned_place_visited_stamps_the_visit_time():
    planned = await journal.create_visit("p1", 42.4, 18.5, status=VisitStatus.WANT_TO_VISIT)
    planned.visited_at = datetime.now(UTC) - timedelta(days=10)  # when it was planned
    await journal.update_visit(planned.id, status=VisitStatus.VISITED)
    assert datetime.now(UTC) - planned.visited_at < timedelta(minutes=1)


async def test_other_edits_leave_the_visit_time_alone():
    visit = await journal.create_visit("p1", 42.4, 18.5)
    visit.visited_at = datetime.now(UTC) - timedelta(days=10)
    stamp = visit.visited_at
    await journal.update_visit(visit.id, status=VisitStatus.VISITED, duration_minutes=30)  # already visited
    assert visit.visited_at == stamp


# ── the empty-text guards also hold for PATCH ──


class TestCommunityEditsKeepTheEmptyGuards:
    @pytest.fixture
    def svc(self, tmp_path):
        return CommunityService(data_dir=str(tmp_path))

    async def test_a_place_cannot_be_renamed_to_nothing(self, svc):
        place = await svc.submit_place(name="Spot", description="", categories=[], lat=1, lng=1, photos=[], tags=[],
                                       author_id="alice")
        with pytest.raises(ValueError, match="name"):
            await svc.update_place(place.id, "alice", name="​ ")
        assert (await svc.update_place(place.id, "alice", name="Better")).name == "Better"

    async def test_a_review_cannot_be_emptied_but_a_tip_may(self, svc):
        place = await svc.submit_place(name="Spot", description="", categories=[], lat=1, lng=1, photos=[], tags=[],
                                       author_id="alice")
        review = await svc.create_review(place.id, "bob", "Lovely")
        with pytest.raises(ValueError, match="Review text"):
            await svc.update_review(review.id, "bob", text="​")
        tip = await svc.create_review(place.id, "carol", "Bring a torch", review_type=ReviewType.TIP)
        assert (await svc.update_review(tip.id, "carol", text="​")).text == ""

    def test_over_http_it_is_a_400(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.community._community_service", None)
        monkeypatch.setattr("app.config.settings.community_data_dir", str(tmp_path))
        created = client.post("/api/community/places", json={
            "name": "Spot", "lat": 42.0, "lng": 18.0, "author_id": "alice"}).json()
        resp = client.patch(f"/api/community/places/{created['id']}?author_id=alice", json={"name": " "})
        assert resp.status_code in (400, 422)


# ── offline pre-cache replaces only the same place ──


class TestMergedIdsReplaceOnlyTheSamePlace:
    def _place(self, pid, lat, lng):
        return Place(id=pid, source=PlaceSource.OSM, name="Old Mill", coordinates=Coordinates(lat=lat, lng=lng))

    def _cache_region(self, merged):
        async def fake(req):
            return DiscoverResponse(places=[merged], total=1, has_more=False)

        with patch("app.api.offline.discover", fake):
            return client.post("/api/offline/places/cache", json={
                "bbox": {"min_lat": 42.9, "min_lng": 18.9, "max_lat": 43.1, "max_lng": 19.1}})

    async def test_a_namesake_in_another_region_survives(self):
        """Deep Research ids derive from the name alone: 'Old Mill' in two towns share one id."""
        await offline_manager.cache_place(self._place("dr_gemini_2797e487", 42.40, 18.50))  # region A
        merged = self._place("osm_way_9", 43.00, 19.00)                                     # region B, 100 km away
        merged.metadata["merged_ids"] = ["dr_gemini_2797e487"]
        assert self._cache_region(merged).status_code == 200
        assert await offline_manager.get_cached_place("dr_gemini_2797e487") is not None
        assert await offline_manager.get_cached_place("osm_way_9") is not None

    async def test_the_same_place_under_an_older_id_is_replaced(self):
        await offline_manager.cache_place(self._place("dr_gemini_2797e487", 43.0005, 19.0005))
        merged = self._place("osm_way_9", 43.00, 19.00)
        merged.metadata["merged_ids"] = ["dr_gemini_2797e487"]
        self._cache_region(merged)
        assert await offline_manager.get_cached_place("dr_gemini_2797e487") is None


def test_the_route_id_parameter_says_what_it_needs():
    from app.models.offline import CachePlacesRequest

    assert "data/routes" in CachePlacesRequest.model_json_schema()["properties"]["route_id"]["description"]


# ── client-chosen text cannot forge log lines ──


async def test_a_trip_name_with_a_newline_stays_on_one_log_line(caplog):
    with caplog.at_level("INFO", logger="app.services.journal"):
        await journal.create_trip("Alps\n2026-10-23 12:00:00 ERROR app.x: forged")
    created = [r.getMessage() for r in caplog.records if "Created trip" in r.getMessage()]
    assert created and all("\n" not in m for m in created)
