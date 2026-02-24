"""Tests for Epic 5 — Explorer Journal (all 4 stories)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.journal import (
    ExplorationStats,
    PlaceRating,
    Trip,
    Visit,
    VisitStatus,
)
from app.services.journal import _reset_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_store():
    """Reset journal store before each test."""
    _reset_store()
    yield
    _reset_store()


# ── Story 5.1: Visit Tracking ───────────────────────────────────


class TestVisitTracking:
    def test_create_visit(self):
        resp = client.post("/api/visits", json={
            "place_id": "osm_node_123",
            "place_name": "Old Bunker",
            "lat": 42.45,
            "lng": 18.53,
            "status": "visited",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["place_id"] == "osm_node_123"
        assert data["place_name"] == "Old Bunker"
        assert data["status"] == "visited"
        assert data["id"].startswith("visit_")

    def test_create_visit_want_to_visit(self):
        resp = client.post("/api/visits", json={
            "place_id": "osm_node_456",
            "lat": 42.46,
            "lng": 18.54,
            "status": "want_to_visit",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "want_to_visit"

    def test_create_visit_skip(self):
        resp = client.post("/api/visits", json={
            "place_id": "osm_node_789",
            "lat": 42.47,
            "lng": 18.55,
            "status": "skip",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "skip"

    def test_list_visits(self):
        # Create 3 visits
        for i in range(3):
            client.post("/api/visits", json={
                "place_id": f"place_{i}",
                "lat": 42.45 + i * 0.01,
                "lng": 18.53 + i * 0.01,
            })
        resp = client.get("/api/visits")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["visits"]) == 3

    def test_list_visits_filter_by_status(self):
        client.post("/api/visits", json={"place_id": "p1", "lat": 42.0, "lng": 18.0, "status": "visited"})
        client.post("/api/visits", json={"place_id": "p2", "lat": 42.1, "lng": 18.1, "status": "want_to_visit"})

        resp = client.get("/api/visits?status=visited")
        assert resp.json()["total"] == 1
        assert resp.json()["visits"][0]["status"] == "visited"

    def test_get_visit(self):
        create_resp = client.post("/api/visits", json={
            "place_id": "p1", "lat": 42.0, "lng": 18.0
        })
        visit_id = create_resp.json()["id"]
        resp = client.get(f"/api/visits/{visit_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == visit_id

    def test_get_visit_not_found(self):
        resp = client.get("/api/visits/nonexistent")
        assert resp.status_code == 404

    def test_update_visit(self):
        create_resp = client.post("/api/visits", json={
            "place_id": "p1", "lat": 42.0, "lng": 18.0
        })
        visit_id = create_resp.json()["id"]
        resp = client.patch(f"/api/visits/{visit_id}", json={
            "status": "skip",
            "duration_minutes": 30.0,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "skip"
        assert resp.json()["duration_minutes"] == 30.0

    def test_delete_visit(self):
        create_resp = client.post("/api/visits", json={
            "place_id": "p1", "lat": 42.0, "lng": 18.0
        })
        visit_id = create_resp.json()["id"]
        resp = client.delete(f"/api/visits/{visit_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        resp2 = client.get(f"/api/visits/{visit_id}")
        assert resp2.status_code == 404

    def test_proximity_check_nearby(self):
        client.post("/api/visits", json={
            "place_id": "bunker_1",
            "place_name": "Old Bunker",
            "lat": 42.4500,
            "lng": 18.5300,
        })
        resp = client.post("/api/visits/proximity", json={
            "lat": 42.4501,
            "lng": 18.5301,
            "radius_m": 100,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["nearby_place_id"] == "bunker_1"
        assert data["already_visited"] is True
        assert data["distance_m"] is not None

    def test_proximity_check_far(self):
        client.post("/api/visits", json={
            "place_id": "bunker_1", "lat": 42.45, "lng": 18.53,
        })
        resp = client.post("/api/visits/proximity", json={
            "lat": 43.0, "lng": 19.0, "radius_m": 100,
        })
        assert resp.status_code == 200
        assert resp.json()["nearby_place_id"] is None

    def test_visited_place_ids(self):
        client.post("/api/visits", json={"place_id": "p1", "lat": 42.0, "lng": 18.0, "status": "visited"})
        client.post("/api/visits", json={"place_id": "p2", "lat": 42.1, "lng": 18.1, "status": "want_to_visit"})
        client.post("/api/visits", json={"place_id": "p3", "lat": 42.2, "lng": 18.2, "status": "visited"})

        resp = client.get("/api/visits/place-ids")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["place_ids"]) == {"p1", "p3"}
        assert data["total"] == 2

    def test_create_visit_with_duration(self):
        resp = client.post("/api/visits", json={
            "place_id": "p1", "lat": 42.0, "lng": 18.0,
            "duration_minutes": 45.0,
        })
        assert resp.status_code == 200
        assert resp.json()["duration_minutes"] == 45.0

    def test_visit_validation_lat_out_of_range(self):
        resp = client.post("/api/visits", json={
            "place_id": "p1", "lat": 100, "lng": 18.0,
        })
        assert resp.status_code == 422


# ── Story 5.2: Place Notes & Rating ─────────────────────────────


class TestPlaceNotes:
    def _create_visit(self) -> str:
        resp = client.post("/api/visits", json={
            "place_id": "test_place", "place_name": "Test Place",
            "lat": 42.0, "lng": 18.0,
        })
        return resp.json()["id"]

    def test_create_note(self):
        visit_id = self._create_visit()
        resp = client.post(f"/api/visits/{visit_id}/notes", json={
            "text": "Amazing abandoned bunker!",
            "rating": {"atmosphere": 5, "accessibility": 3, "photogenic": 4, "uniqueness": 5},
            "tags": ["urbex", "best-of-2025"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Amazing abandoned bunker!"
        assert data["rating"]["atmosphere"] == 5
        assert data["tags"] == ["urbex", "best-of-2025"]
        assert data["id"].startswith("note_")

    def test_create_note_visit_not_found(self):
        resp = client.post("/api/visits/nonexistent/notes", json={"text": "test"})
        assert resp.status_code == 404

    def test_get_notes_for_visit(self):
        visit_id = self._create_visit()
        client.post(f"/api/visits/{visit_id}/notes", json={"text": "Note 1"})
        client.post(f"/api/visits/{visit_id}/notes", json={"text": "Note 2"})

        resp = client.get(f"/api/visits/{visit_id}/notes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_update_note(self):
        visit_id = self._create_visit()
        create_resp = client.post(f"/api/visits/{visit_id}/notes", json={"text": "Original"})
        note_id = create_resp.json()["id"]

        resp = client.patch(f"/api/notes/{note_id}", json={"text": "Updated text"})
        assert resp.status_code == 200
        assert resp.json()["text"] == "Updated text"

    def test_delete_note(self):
        visit_id = self._create_visit()
        create_resp = client.post(f"/api/visits/{visit_id}/notes", json={"text": "To delete"})
        note_id = create_resp.json()["id"]

        resp = client.delete(f"/api/notes/{note_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_note_with_voice_transcript(self):
        visit_id = self._create_visit()
        resp = client.post(f"/api/visits/{visit_id}/notes", json={
            "voice_transcript": "This is a voice memo transcription",
        })
        assert resp.status_code == 200
        assert resp.json()["voice_transcript"] == "This is a voice memo transcription"

    def test_note_with_photos(self):
        visit_id = self._create_visit()
        resp = client.post(f"/api/visits/{visit_id}/notes", json={
            "photos": ["https://example.com/photo1.jpg", "/local/photo2.jpg"],
        })
        assert resp.status_code == 200
        assert len(resp.json()["photos"]) == 2

    def test_rating_average(self):
        rating = PlaceRating(atmosphere=4, accessibility=3, photogenic=5, uniqueness=4)
        assert rating.average == 4.0

    def test_rating_average_partial(self):
        rating = PlaceRating(atmosphere=4, accessibility=0, photogenic=5, uniqueness=0)
        assert rating.average == 4.5

    def test_rating_average_empty(self):
        rating = PlaceRating()
        assert rating.average == 0.0

    def test_delete_visit_cascades_notes(self):
        visit_id = self._create_visit()
        client.post(f"/api/visits/{visit_id}/notes", json={"text": "Note 1"})
        client.post(f"/api/visits/{visit_id}/notes", json={"text": "Note 2"})

        client.delete(f"/api/visits/{visit_id}")
        # Notes should be gone
        resp = client.get(f"/api/visits/{visit_id}/notes")
        # Visit not found, but notes endpoint just returns empty
        assert resp.json()["total"] == 0


# ── Story 5.3: Exploration Statistics ────────────────────────────


class TestExplorationStats:
    def test_empty_stats(self):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        stats = resp.json()["stats"]
        assert stats["total_visited"] == 0
        assert stats["streak"]["current_streak"] == 0

    def test_stats_with_visits(self):
        client.post("/api/visits", json={"place_id": "p1", "lat": 42.0, "lng": 18.0, "status": "visited", "duration_minutes": 30})
        client.post("/api/visits", json={"place_id": "p2", "lat": 42.1, "lng": 18.1, "status": "visited", "duration_minutes": 60})
        client.post("/api/visits", json={"place_id": "p3", "lat": 42.2, "lng": 18.2, "status": "want_to_visit"})

        resp = client.get("/api/stats")
        stats = resp.json()["stats"]
        assert stats["total_visited"] == 2
        assert stats["total_want_to_visit"] == 1
        assert stats["total_hours"] == 1.5  # 90 min
        assert stats["total_distance_km"] > 0

    def test_heatmap_empty(self):
        resp = client.get("/api/stats/heatmap")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_heatmap_with_data(self):
        client.post("/api/visits", json={"place_id": "p1", "lat": 42.0, "lng": 18.0, "status": "visited"})
        client.post("/api/visits", json={"place_id": "p2", "lat": 42.1, "lng": 18.1, "status": "visited"})

        resp = client.get("/api/stats/heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_stats_by_category_from_tags(self):
        v_resp = client.post("/api/visits", json={"place_id": "p1", "lat": 42.0, "lng": 18.0})
        visit_id = v_resp.json()["id"]
        client.post(f"/api/visits/{visit_id}/notes", json={
            "tags": ["urbex", "military"],
        })
        v2_resp = client.post("/api/visits", json={"place_id": "p2", "lat": 42.1, "lng": 18.1})
        visit_id2 = v2_resp.json()["id"]
        client.post(f"/api/visits/{visit_id2}/notes", json={
            "tags": ["urbex"],
        })

        resp = client.get("/api/stats")
        stats = resp.json()["stats"]
        cats = {c["category"]: c["count"] for c in stats["by_category"]}
        assert cats.get("urbex", 0) == 2
        assert cats.get("military", 0) == 1


# ── Story 5.4: Trip Organization ────────────────────────────────


class TestTripOrganization:
    def test_create_trip(self):
        resp = client.post("/api/trips", json={
            "name": "Montenegro 2025",
            "region": "Kotor Bay",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Montenegro 2025"
        assert data["region"] == "Kotor Bay"
        assert data["id"].startswith("trip_")

    def test_list_trips(self):
        client.post("/api/trips", json={"name": "Trip 1"})
        client.post("/api/trips", json={"name": "Trip 2"})

        resp = client.get("/api/trips")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_get_trip(self):
        create_resp = client.post("/api/trips", json={"name": "Test Trip"})
        trip_id = create_resp.json()["id"]

        resp = client.get(f"/api/trips/{trip_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Trip"

    def test_get_trip_not_found(self):
        resp = client.get("/api/trips/nonexistent")
        assert resp.status_code == 404

    def test_update_trip(self):
        create_resp = client.post("/api/trips", json={"name": "Original"})
        trip_id = create_resp.json()["id"]

        resp = client.patch(f"/api/trips/{trip_id}", json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_delete_trip(self):
        create_resp = client.post("/api/trips", json={"name": "To Delete"})
        trip_id = create_resp.json()["id"]

        resp = client.delete(f"/api/trips/{trip_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_create_visit_with_trip(self):
        trip_resp = client.post("/api/trips", json={"name": "Trip"})
        trip_id = trip_resp.json()["id"]

        visit_resp = client.post("/api/visits", json={
            "place_id": "p1", "lat": 42.0, "lng": 18.0,
            "trip_id": trip_id,
        })
        visit_id = visit_resp.json()["id"]

        # Trip should have the visit
        trip = client.get(f"/api/trips/{trip_id}").json()
        assert visit_id in trip["visit_ids"]

    def test_trip_summary(self):
        trip_resp = client.post("/api/trips", json={"name": "Summary Test"})
        trip_id = trip_resp.json()["id"]

        v_resp = client.post("/api/visits", json={
            "place_id": "p1", "place_name": "Bunker",
            "lat": 42.0, "lng": 18.0,
            "trip_id": trip_id, "duration_minutes": 30,
        })
        visit_id = v_resp.json()["id"]
        client.post(f"/api/visits/{visit_id}/notes", json={
            "text": "Great place",
            "rating": {"atmosphere": 5, "accessibility": 3, "photogenic": 4, "uniqueness": 5},
            "tags": ["urbex"],
        })

        resp = client.get(f"/api/trips/{trip_id}/summary")
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert summary["total_places"] == 1
        assert summary["total_hours"] == 0.5
        assert len(summary["best_rated_places"]) == 1

    def test_trip_export_markdown(self):
        trip_resp = client.post("/api/trips", json={"name": "Export Test", "region": "Montenegro"})
        trip_id = trip_resp.json()["id"]

        client.post("/api/visits", json={
            "place_id": "p1", "place_name": "Fortress",
            "lat": 42.0, "lng": 18.0, "trip_id": trip_id,
        })

        resp = client.post(f"/api/trips/{trip_id}/export", json={"format": "markdown"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Export Test" in data["content"]
        assert data["format"] == "markdown"
        assert data["filename"].endswith(".md")

    def test_trip_export_json(self):
        trip_resp = client.post("/api/trips", json={"name": "JSON Export"})
        trip_id = trip_resp.json()["id"]

        resp = client.post(f"/api/trips/{trip_id}/export", json={"format": "json"})
        assert resp.status_code == 200
        assert resp.json()["format"] == "json"
        assert resp.json()["filename"].endswith(".json")

    def test_auto_group_visits(self):
        trip_resp = client.post("/api/trips", json={"name": "Auto Group"})
        trip_id = trip_resp.json()["id"]

        # Create visits
        client.post("/api/visits", json={"place_id": "p1", "lat": 42.0, "lng": 18.0})
        client.post("/api/visits", json={"place_id": "p2", "lat": 42.01, "lng": 18.01})
        client.post("/api/visits", json={"place_id": "p3", "lat": 50.0, "lng": 30.0})  # far away

        now = datetime.now(UTC)
        resp = client.post(f"/api/trips/{trip_id}/auto-group", json={
            "start_date": (now - timedelta(hours=1)).isoformat(),
            "end_date": (now + timedelta(hours=1)).isoformat(),
            "region_lat": 42.0,
            "region_lng": 18.0,
            "region_radius_km": 10.0,
        })
        assert resp.status_code == 200
        data = resp.json()
        # Should include p1 and p2 but not p3 (too far)
        assert len(data["visit_ids"]) == 2

    def test_delete_trip_unlinks_visits(self):
        trip_resp = client.post("/api/trips", json={"name": "Unlink Test"})
        trip_id = trip_resp.json()["id"]

        v_resp = client.post("/api/visits", json={
            "place_id": "p1", "lat": 42.0, "lng": 18.0,
            "trip_id": trip_id,
        })
        visit_id = v_resp.json()["id"]

        client.delete(f"/api/trips/{trip_id}")

        # Visit should still exist but with no trip
        visit = client.get(f"/api/visits/{visit_id}").json()
        assert visit["trip_id"] is None

    def test_trip_summary_not_found(self):
        resp = client.get("/api/trips/nonexistent/summary")
        assert resp.status_code == 404

    def test_trip_export_not_found(self):
        resp = client.post("/api/trips/nonexistent/export", json={"format": "markdown"})
        assert resp.status_code == 404


# ── Edge Cases & Integration ─────────────────────────────────────


class TestJournalEdgeCases:
    def test_multiple_visits_same_place(self):
        """Can visit the same place multiple times."""
        client.post("/api/visits", json={"place_id": "p1", "lat": 42.0, "lng": 18.0})
        client.post("/api/visits", json={"place_id": "p1", "lat": 42.0, "lng": 18.0})
        resp = client.get("/api/visits")
        assert resp.json()["total"] == 2

    def test_update_nonexistent_visit(self):
        resp = client.patch("/api/visits/nonexistent", json={"status": "skip"})
        assert resp.status_code == 404

    def test_delete_nonexistent_visit(self):
        resp = client.delete("/api/visits/nonexistent")
        assert resp.status_code == 404

    def test_update_nonexistent_note(self):
        resp = client.patch("/api/notes/nonexistent", json={"text": "test"})
        assert resp.status_code == 404

    def test_delete_nonexistent_note(self):
        resp = client.delete("/api/notes/nonexistent")
        assert resp.status_code == 404

    def test_list_visits_pagination(self):
        for i in range(5):
            client.post("/api/visits", json={"place_id": f"p{i}", "lat": 42.0, "lng": 18.0})

        resp = client.get("/api/visits?limit=2&offset=0")
        assert len(resp.json()["visits"]) == 2
        assert resp.json()["total"] == 5

        resp2 = client.get("/api/visits?limit=2&offset=2")
        assert len(resp2.json()["visits"]) == 2

    def test_trip_with_dates(self):
        now = datetime.now(UTC)
        resp = client.post("/api/trips", json={
            "name": "Dated Trip",
            "start_date": now.isoformat(),
            "end_date": (now + timedelta(days=7)).isoformat(),
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["start_date"] is not None
        assert data["end_date"] is not None

    def test_full_workflow(self):
        """Integration test: create trip → add visits → add notes → get summary → export."""
        # 1. Create trip
        trip_resp = client.post("/api/trips", json={
            "name": "Full Workflow Trip",
            "region": "Kotor Bay",
        })
        trip_id = trip_resp.json()["id"]

        # 2. Add visits
        v1 = client.post("/api/visits", json={
            "place_id": "fortress_1",
            "place_name": "St. John Fortress",
            "lat": 42.4247,
            "lng": 18.7712,
            "trip_id": trip_id,
            "duration_minutes": 120,
        }).json()

        v2 = client.post("/api/visits", json={
            "place_id": "bunker_1",
            "place_name": "WWII Bunker",
            "lat": 42.4350,
            "lng": 18.7600,
            "trip_id": trip_id,
            "duration_minutes": 45,
        }).json()

        # 3. Add notes
        client.post(f"/api/visits/{v1['id']}/notes", json={
            "text": "Incredible views from the top!",
            "rating": {"atmosphere": 5, "accessibility": 2, "photogenic": 5, "uniqueness": 4},
            "tags": ["landmark", "viewpoint"],
        })

        client.post(f"/api/visits/{v2['id']}/notes", json={
            "text": "Eerie atmosphere inside",
            "rating": {"atmosphere": 5, "accessibility": 3, "photogenic": 4, "uniqueness": 5},
            "tags": ["urbex", "military"],
        })

        # 4. Get summary
        summary = client.get(f"/api/trips/{trip_id}/summary").json()["summary"]
        assert summary["total_places"] == 2
        assert summary["total_hours"] == 2.75  # (120 + 45) / 60
        assert len(summary["best_rated_places"]) == 2

        # 5. Export markdown
        export = client.post(f"/api/trips/{trip_id}/export", json={"format": "markdown"}).json()
        assert "St. John Fortress" in export["content"]
        assert "WWII Bunker" in export["content"]

        # 6. Stats
        stats = client.get("/api/stats").json()["stats"]
        assert stats["total_visited"] == 2
        assert stats["total_distance_km"] > 0

        # 7. Heatmap
        heatmap = client.get("/api/stats/heatmap").json()
        assert heatmap["total"] == 2


class TestDwellCheck:
    def test_dwell_check_should_prompt(self):
        resp = client.post("/api/visits/dwell-check", json={
            "place_id": "bunker_1",
            "place_name": "Old Bunker",
            "lat": 42.45,
            "lng": 18.53,
            "dwell_minutes": 6.0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["should_prompt"] is True
        assert data["already_visited"] is False

    def test_dwell_check_too_short(self):
        resp = client.post("/api/visits/dwell-check", json={
            "place_id": "bunker_1",
            "lat": 42.45,
            "lng": 18.53,
            "dwell_minutes": 3.0,
        })
        assert resp.status_code == 200
        assert resp.json()["should_prompt"] is False

    def test_dwell_check_already_visited(self):
        client.post("/api/visits", json={
            "place_id": "bunker_1",
            "lat": 42.45,
            "lng": 18.53,
            "status": "visited",
        })
        resp = client.post("/api/visits/dwell-check", json={
            "place_id": "bunker_1",
            "lat": 42.45,
            "lng": 18.53,
            "dwell_minutes": 10.0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["should_prompt"] is False
        assert data["already_visited"] is True


class TestHTMLExport:
    def test_trip_export_html(self):
        trip_resp = client.post("/api/trips", json={"name": "HTML Test", "region": "Kotor"})
        trip_id = trip_resp.json()["id"]

        v = client.post("/api/visits", json={
            "place_id": "p1", "place_name": "Fortress",
            "lat": 42.0, "lng": 18.0, "trip_id": trip_id,
        }).json()
        client.post(f"/api/visits/{v['id']}/notes", json={
            "text": "Beautiful place!",
            "rating": {"atmosphere": 5, "accessibility": 3, "photogenic": 4, "uniqueness": 5},
        })

        resp = client.post(f"/api/trips/{trip_id}/export", json={"format": "html"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "html"
        assert data["filename"].endswith(".html")
        assert "<!DOCTYPE html>" in data["content"]
        assert "Fortress" in data["content"]
        assert "Terra Incognita" in data["content"]

    def test_html_export_escapes_xss(self):
        trip_resp = client.post("/api/trips", json={"name": "XSS <script>alert(1)</script>"})
        trip_id = trip_resp.json()["id"]

        resp = client.post(f"/api/trips/{trip_id}/export", json={"format": "html"})
        assert resp.status_code == 200
        assert "<script>" not in resp.json()["content"]
        assert "&lt;script&gt;" in resp.json()["content"]


class TestInputSanitization:
    def _create_visit(self) -> str:
        resp = client.post("/api/visits", json={
            "place_id": "test_place", "lat": 42.0, "lng": 18.0,
        })
        return resp.json()["id"]

    def test_note_text_sanitized(self):
        visit_id = self._create_visit()
        resp = client.post(f"/api/visits/{visit_id}/notes", json={
            "text": "Normal text\x00with\x01control\x02chars",
        })
        assert resp.status_code == 200
        assert "\x00" not in resp.json()["text"]
        assert "Normal text" in resp.json()["text"]

    def test_tags_sanitized(self):
        visit_id = self._create_visit()
        resp = client.post(f"/api/visits/{visit_id}/notes", json={
            "tags": ["valid-tag", "another_tag", "<script>xss</script>"],
        })
        assert resp.status_code == 200
        tags = resp.json()["tags"]
        assert "valid-tag" in tags
        assert "another_tag" in tags
        # XSS tag should be sanitized (no special chars)
        for tag in tags:
            assert "<" not in tag

    def test_photo_urls_validated(self):
        visit_id = self._create_visit()
        resp = client.post(f"/api/visits/{visit_id}/notes", json={
            "photos": ["https://example.com/photo.jpg", "javascript:alert(1)", "/local/photo.jpg"],
        })
        assert resp.status_code == 200
        photos = resp.json()["photos"]
        assert "https://example.com/photo.jpg" in photos
        assert "/local/photo.jpg" in photos
        assert "javascript:alert(1)" not in photos
