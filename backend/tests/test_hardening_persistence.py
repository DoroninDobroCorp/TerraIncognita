"""Persistence hardening: corrupt store files must never be silently destroyed."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.models.journal import Visit
from app.models.place import Coordinates
from app.services import gamification as gam
from app.services import journal
from app.services.community import CommunityService
from app.utils.atomic_io import atomic_write_text, load_json_file


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


class TestAtomicIo:
    def test_atomic_write_leaves_no_temp_files(self, tmp_path):
        target = tmp_path / "a.json"
        atomic_write_text(target, "one")
        atomic_write_text(target, "two")
        assert target.read_text() == "two"
        assert [p.name for p in tmp_path.iterdir()] == ["a.json"]

    def test_load_json_file_quarantines_garbage(self, tmp_path):
        target = tmp_path / "s.json"
        target.write_text("{oops", encoding="utf-8")
        assert load_json_file(target) is None
        backups = list(tmp_path.glob("s.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "{oops"

    def test_load_json_file_missing_is_none_without_backup(self, tmp_path):
        assert load_json_file(tmp_path / "nope.json") is None
        assert list(tmp_path.iterdir()) == []


class TestJournalCorruptStore:
    async def test_unparseable_visits_file_is_kept_as_backup(self):
        data_dir = journal._get_data_dir()
        (data_dir / "visits.json").write_text("{not json", encoding="utf-8")

        await journal.create_visit("fresh", 1.0, 2.0)  # triggers a save over visits.json

        backups = list(data_dir.glob("visits.json.corrupt-*"))
        assert len(backups) == 1, "the unreadable file was overwritten without a backup"
        assert backups[0].read_text() == "{not json"

    async def test_one_bad_record_does_not_hide_the_good_ones(self):
        data_dir = journal._get_data_dir()
        raw = {"bad": {"id": "bad"}, "good": _visit_json("good")}
        (data_dir / "visits.json").write_text(json.dumps(raw), encoding="utf-8")

        visits, total = await journal.list_visits()

        assert total == 1
        assert visits[0].id == "good"
        backups = list(data_dir.glob("visits.json.corrupt-*"))
        assert len(backups) == 1
        assert "bad" in backups[0].read_text()

    async def test_quarantine_happens_once_not_on_every_call(self):
        data_dir = journal._get_data_dir()
        (data_dir / "visits.json").write_text("{not json", encoding="utf-8")
        for _ in range(3):
            await journal.list_visits()
        assert len(list(data_dir.glob("visits.json.corrupt-*"))) == 1


class TestGamificationCorruptStore:
    async def test_corrupt_fog_file_is_kept_as_backup(self):
        data_dir = gam._get_data_dir()
        (data_dir / "fog_cells.json").write_text("[1, 2", encoding="utf-8")

        await gam.reveal_fog([(42.45, 18.53, None)], radius_m=50)

        backups = list(data_dir.glob("fog_cells.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "[1, 2"

    async def test_corrupt_xp_file_is_kept_as_backup(self):
        data_dir = gam._get_data_dir()
        (data_dir / "xp_events.json").write_text("garbage", encoding="utf-8")

        await gam._award_xp(10, "t", "t")

        assert len(list(data_dir.glob("xp_events.json.corrupt-*"))) == 1


class TestCommunityCorruptStore:
    async def test_corrupt_places_file_is_kept_as_backup(self, tmp_path):
        (tmp_path / "places.json").write_text("{broken", encoding="utf-8")
        svc = CommunityService(data_dir=str(tmp_path))

        await svc.submit_place(
            name="N", description="", categories=[], lat=1, lng=1,
            photos=[], tags=[], author_id="u1",
        )

        backups = list(tmp_path.glob("places.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "{broken"
