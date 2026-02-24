"""Gamification tests (Epic 6): Fog of War, Achievements, Explorer Level."""

from __future__ import annotations

import pytest
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.journal import PlaceNote, PlaceRating, Visit, VisitStatus
from app.models.place import Coordinates
from app.services import gamification as gam_service


@pytest.fixture(autouse=True)
def reset_gamification():
    """Reset gamification state before each test."""
    gam_service._reset_store()
    yield
    gam_service._reset_store()


@pytest.fixture
def sample_visits() -> list[Visit]:
    """Create sample visits for testing."""
    now = datetime.now(UTC)
    return [
        Visit(
            id=f"visit_{i}",
            place_id=f"place_{i}",
            place_name=f"Test Place {i}",
            status=VisitStatus.VISITED,
            coordinates=Coordinates(lat=42.45 + i * 0.005, lng=18.53 + i * 0.005),
            visited_at=now - timedelta(days=i),
        )
        for i in range(15)
    ]


@pytest.fixture
def sample_notes() -> list[PlaceNote]:
    """Create sample notes for testing."""
    now = datetime.now(UTC)
    return [
        PlaceNote(
            id=f"note_{i}",
            visit_id=f"visit_{i}",
            place_id=f"place_{i}",
            text=f"Test note {i}",
            rating=PlaceRating(atmosphere=4, uniqueness=3),
            tags=["abandoned", "underground"] if i % 2 == 0 else ["viewpoint"],
            photos=[f"https://example.com/photo_{i}.jpg"] if i < 5 else [],
            created_at=now - timedelta(days=i),
        )
        for i in range(12)
    ]


# ── Story 6.1: Fog of War Tests ─────────────────────────────────


class TestFogOfWar:
    """Tests for Fog of War mechanics."""

    async def test_reveal_fog_single_point(self):
        """Reveal fog at a single GPS point."""
        result = await gam_service.reveal_fog(
            [(42.45, 18.53, datetime.now(UTC))],
            radius_m=50.0,
        )
        assert result.new_cells_revealed > 0
        assert result.total_explored_cells > 0
        assert result.total_explored_area_km2 > 0

    async def test_reveal_fog_multiple_points(self):
        """Reveal fog from a GPS trail."""
        points = [
            (42.45 + i * 0.001, 18.53, datetime.now(UTC))
            for i in range(10)
        ]
        result = await gam_service.reveal_fog(points, radius_m=50.0)
        assert result.new_cells_revealed > 0
        assert result.total_explored_cells >= result.new_cells_revealed

    async def test_reveal_fog_no_double_count(self):
        """Revealing same area twice shouldn't add new cells."""
        point = [(42.45, 18.53, datetime.now(UTC))]
        r1 = await gam_service.reveal_fog(point, radius_m=50.0)
        r2 = await gam_service.reveal_fog(point, radius_m=50.0)
        assert r2.new_cells_revealed == 0
        assert r2.total_explored_cells == r1.total_explored_cells

    async def test_fog_status(self):
        """Get fog of war status after revealing."""
        await gam_service.reveal_fog(
            [(42.45, 18.53, datetime.now(UTC))], radius_m=50.0
        )
        status = await gam_service.get_fog_status()
        assert status.total_explored_cells > 0
        assert status.total_explored_area_km2 > 0

    async def test_fog_region_stats(self):
        """Get fog coverage for a specific region."""
        await gam_service.reveal_fog(
            [(42.45, 18.53, datetime.now(UTC))], radius_m=100.0
        )
        stats = await gam_service.get_fog_region(42.45, 18.53, 1.0, "test_city")
        assert stats.region_name == "test_city"
        assert stats.explored_cells > 0
        assert 0 < stats.coverage_percent <= 100

    async def test_explored_cells_in_area(self):
        """Get explored cells within a specific area."""
        await gam_service.reveal_fog(
            [(42.45, 18.53, datetime.now(UTC))], radius_m=50.0
        )
        cells = await gam_service.get_explored_cells_in_area(42.45, 18.53, 1.0)
        assert len(cells) > 0
        for cell in cells:
            assert hasattr(cell, "lat")
            assert hasattr(cell, "lng")

    async def test_fog_empty_initially(self):
        """Fog should be completely unexplored initially."""
        status = await gam_service.get_fog_status()
        assert status.total_explored_cells == 0
        assert status.total_explored_area_km2 == 0

    async def test_fog_persistence(self):
        """Fog data is saved and loadable."""
        await gam_service.reveal_fog(
            [(42.45, 18.53, datetime.now(UTC))], radius_m=50.0
        )
        # Verify file was written
        data_dir = gam_service._get_data_dir()
        assert (data_dir / "fog_cells.json").exists()


# ── Story 6.2: Achievement System Tests ─────────────────────────


class TestAchievements:
    """Tests for Achievement System."""

    async def test_first_visit_achievement(self, sample_visits, sample_notes):
        """First visit should unlock 'First Steps' achievement."""
        result = await gam_service.check_achievements(
            visits=sample_visits[:1],
            notes=sample_notes[:1],
        )
        unlocked_ids = [a.definition.id for a in result.new_achievements]
        assert "first_visit" in unlocked_ids

    async def test_visits_10_achievement(self, sample_visits, sample_notes):
        """10 visits should unlock 'Getting Started'."""
        result = await gam_service.check_achievements(
            visits=sample_visits[:10],
            notes=sample_notes[:10],
        )
        unlocked_ids = [a.definition.id for a in result.new_achievements]
        assert "visits_10" in unlocked_ids

    async def test_streak_achievement(self, sample_visits, sample_notes):
        """3-day streak should unlock 'Three-peat'."""
        result = await gam_service.check_achievements(
            visits=sample_visits,
            notes=sample_notes,
            streak_days=3,
        )
        unlocked_ids = [a.definition.id for a in result.new_achievements]
        assert "streak_3" in unlocked_ids

    async def test_no_duplicate_achievements(self, sample_visits, sample_notes):
        """Same achievement should not unlock twice."""
        r1 = await gam_service.check_achievements(
            visits=sample_visits[:1],
            notes=sample_notes[:1],
        )
        assert any(a.definition.id == "first_visit" for a in r1.new_achievements)

        r2 = await gam_service.check_achievements(
            visits=sample_visits[:1],
            notes=sample_notes[:1],
        )
        assert not any(a.definition.id == "first_visit" for a in r2.new_achievements)

    async def test_achievement_xp_reward(self, sample_visits, sample_notes):
        """Unlocking achievements should grant XP."""
        result = await gam_service.check_achievements(
            visits=sample_visits[:1],
            notes=sample_notes[:1],
        )
        assert result.xp_earned > 0

    async def test_get_achievements_list(self, sample_visits, sample_notes):
        """Get all achievements with progress."""
        await gam_service.check_achievements(
            visits=sample_visits[:5],
            notes=sample_notes[:5],
        )
        achievements, unlocked, total = await gam_service.get_achievements()
        assert total == len(gam_service.ACHIEVEMENTS)
        assert unlocked > 0
        assert len(achievements) == total

    async def test_hidden_achievements_masked(self):
        """Hidden achievements should show as '???' until unlocked."""
        achievements, _, _ = await gam_service.get_achievements()
        hidden = [a for a in achievements if a.definition.hidden]
        for h in hidden:
            assert h.definition.name == "???"
            assert h.definition.icon == "🔒"

    async def test_category_achievements(self, sample_visits, sample_notes):
        """Category-specific achievements should track correctly."""
        # Notes with "abandoned" tag should count
        result = await gam_service.check_achievements(
            visits=sample_visits,
            notes=sample_notes,
        )
        achievements, _, _ = await gam_service.get_achievements()
        abandoned_ach = next(
            (a for a in achievements if a.definition.id == "abandoned_10"),
            None,
        )
        assert abandoned_ach is not None
        assert abandoned_ach.progress_percent > 0

    async def test_distance_achievement(self, sample_visits, sample_notes):
        """Distance achievements should track total_distance_km."""
        result = await gam_service.check_achievements(
            visits=sample_visits,
            notes=sample_notes,
            total_distance_km=15.0,
        )
        unlocked_ids = [a.definition.id for a in result.new_achievements]
        assert "distance_10" in unlocked_ids

    async def test_night_visit_achievement(self):
        """Night visit (0-5 AM) should unlock hidden achievement."""
        night_visit = Visit(
            id="visit_night",
            place_id="place_night",
            place_name="Night Spot",
            status=VisitStatus.VISITED,
            coordinates=Coordinates(lat=42.45, lng=18.53),
            visited_at=datetime(2026, 1, 15, 2, 30, tzinfo=UTC),
        )
        result = await gam_service.check_achievements(
            visits=[night_visit],
            notes=[],
        )
        unlocked_ids = [a.definition.id for a in result.new_achievements]
        assert "night_explorer" in unlocked_ids

    async def test_unique_categories_achievement(self, sample_visits, sample_notes):
        """Visiting 5 categories should unlock 'Category Collector'."""
        # Add notes with diverse categories
        now = datetime.now(UTC)
        diverse_notes = []
        cats = ["abandoned", "underground", "viewpoint", "cave", "ruins"]
        for i, cat in enumerate(cats):
            diverse_notes.append(PlaceNote(
                id=f"note_div_{i}",
                visit_id=f"visit_{i}",
                place_id=f"place_{i}",
                tags=[cat],
                created_at=now,
            ))
        result = await gam_service.check_achievements(
            visits=sample_visits[:5],
            notes=diverse_notes,
        )
        unlocked_ids = [a.definition.id for a in result.new_achievements]
        assert "region_diverse" in unlocked_ids


# ── Story 6.3: Explorer Level Tests ─────────────────────────────


class TestExplorerLevel:
    """Tests for Explorer Level & XP system."""

    async def test_initial_profile(self):
        """New explorer should be Novice with 0 XP."""
        profile = await gam_service.get_explorer_profile()
        assert profile.total_xp == 0
        assert profile.level == gam_service.ExplorerLevel.NOVICE
        assert profile.title == "Novice Explorer"

    async def test_xp_from_visit(self, sample_visits, sample_notes):
        """Visiting a place should award XP."""
        result = await gam_service.award_visit_xp(
            visit=sample_visits[0],
            notes=sample_notes[:1],
        )
        assert result.xp_earned > 0
        assert result.total_xp > 0

    async def test_rare_category_bonus(self, sample_visits, sample_notes):
        """Rare category visits should give more XP."""
        normal_result = await gam_service.award_visit_xp(
            visit=sample_visits[0],
            notes=[],
            is_rare_category=False,
        )
        gam_service._reset_store()
        rare_result = await gam_service.award_visit_xp(
            visit=sample_visits[0],
            notes=[],
            is_rare_category=True,
        )
        assert rare_result.xp_earned > normal_result.xp_earned

    async def test_streak_bonus(self, sample_visits, sample_notes):
        """Streak should multiply XP."""
        result = await gam_service.award_visit_xp(
            visit=sample_visits[0],
            notes=[],
            streak_days=5,
        )
        # Should include base visit XP + streak bonus
        assert result.xp_earned > gam_service.XP_REWARDS["visit_new"]

    async def test_level_up(self):
        """XP should trigger level up."""
        # Award enough XP to reach Scout (100 XP)
        await gam_service._award_xp(99, "test", "test")
        result = await gam_service.award_visit_xp(
            visit=Visit(
                id="v1", place_id="p1", place_name="P1",
                status=VisitStatus.VISITED,
                coordinates=Coordinates(lat=42.45, lng=18.53),
            ),
            notes=[],
        )
        assert result.leveled_up is True
        assert result.new_level == gam_service.ExplorerLevel.SCOUT

    async def test_level_thresholds(self):
        """Verify level thresholds are correct."""
        assert gam_service._get_level(0)[0] == gam_service.ExplorerLevel.NOVICE
        assert gam_service._get_level(100)[0] == gam_service.ExplorerLevel.SCOUT
        assert gam_service._get_level(500)[0] == gam_service.ExplorerLevel.EXPLORER
        assert gam_service._get_level(1500)[0] == gam_service.ExplorerLevel.PATHFINDER
        assert gam_service._get_level(4000)[0] == gam_service.ExplorerLevel.TRAILBLAZER
        assert gam_service._get_level(10000)[0] == gam_service.ExplorerLevel.LEGEND

    async def test_profile_progress(self):
        """Profile should show progress towards next level."""
        await gam_service._award_xp(50, "test", "test")
        profile = await gam_service.get_explorer_profile()
        assert profile.level == gam_service.ExplorerLevel.NOVICE
        assert profile.xp_to_next_level == 50
        assert profile.level_progress_percent == 50.0

    async def test_xp_history(self):
        """XP history should track all events."""
        await gam_service._award_xp(10, "visit", "Visit 1")
        await gam_service._award_xp(25, "visit", "Visit 2")
        events, total = await gam_service.get_xp_history()
        assert total == 2
        assert events[0].xp == 25  # most recent first

    async def test_leaderboard(self):
        """Leaderboard should show current user."""
        await gam_service._award_xp(100, "test", "test")
        board = await gam_service.get_leaderboard()
        assert len(board) == 1
        assert board[0].total_xp == 100
        assert board[0].user_id == "self"

    async def test_note_xp_bonuses(self, sample_visits):
        """Notes with text, photo, rating should each give XP bonus."""
        note_with_all = PlaceNote(
            id="note_full",
            visit_id="visit_0",
            place_id="place_0",
            text="Great place!",
            rating=PlaceRating(atmosphere=5, uniqueness=4),
            photos=["https://example.com/photo.jpg"],
        )
        result = await gam_service.award_visit_xp(
            visit=sample_visits[0],
            notes=[note_with_all],
        )
        expected_min = (
            gam_service.XP_REWARDS["visit_new"]
            + gam_service.XP_REWARDS["note_with_text"]
            + gam_service.XP_REWARDS["note_with_photo"]
            + gam_service.XP_REWARDS["note_with_rating"]
        )
        assert result.xp_earned >= expected_min

    async def test_legend_level_max(self):
        """Legend level should have no further progression."""
        await gam_service._award_xp(15000, "test", "test")
        profile = await gam_service.get_explorer_profile()
        assert profile.level == gam_service.ExplorerLevel.LEGEND
        assert profile.xp_to_next_level == 0
        assert profile.title == "Living Legend"


# ── Integration: on_visit_created Hook Tests ─────────────────────


class TestOnVisitHook:
    """Tests for the integrated on_visit_created hook."""

    async def test_on_visit_created(self, sample_visits, sample_notes):
        """on_visit_created should award XP, reveal fog, check achievements."""
        results = await gam_service.on_visit_created(
            visit=sample_visits[0],
            notes=sample_notes[:1],
            all_visits=sample_visits[:1],
            all_notes=sample_notes[:1],
        )
        assert "fog" in results
        assert "xp" in results
        assert "achievements" in results
        assert results["fog"].new_cells_revealed > 0
        assert results["xp"].xp_earned > 0


# ── Journal → Gamification Integration Tests ─────────────────────


class TestJournalGamificationIntegration:
    """Tests that journal create_visit triggers gamification."""

    @pytest.fixture(autouse=True)
    def reset_journal(self):
        from app.services import journal as journal_service
        journal_service._reset_store()
        yield
        journal_service._reset_store()

    async def test_create_visit_triggers_gamification(self):
        """Creating a visit via journal should trigger gamification automatically."""
        from app.services import journal as journal_service
        visit = await journal_service.create_visit(
            place_id="test_place_1",
            lat=42.45,
            lng=18.53,
            place_name="Test Place",
            status=VisitStatus.VISITED,
        )
        assert visit.id is not None

        # Gamification should have been triggered: fog revealed, XP earned
        profile = await gam_service.get_explorer_profile()
        assert profile.total_xp > 0
        assert profile.fog_explored_km2 > 0

    async def test_want_to_visit_no_gamification(self):
        """Want-to-visit should NOT trigger gamification."""
        from app.services import journal as journal_service
        visit = await journal_service.create_visit(
            place_id="test_place_2",
            lat=42.46,
            lng=18.54,
            place_name="Future Place",
            status=VisitStatus.WANT_TO_VISIT,
        )
        assert visit.id is not None

        profile = await gam_service.get_explorer_profile()
        assert profile.total_xp == 0


# ── API Endpoint Tests ──────────────────────────────────────────


class TestGamificationAPI:
    """Tests for gamification API endpoints."""

    @pytest.fixture
    def client(self):
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def test_fog_reveal_endpoint(self, client):
        """POST /api/fog/reveal should work."""
        resp = await client.post("/api/fog/reveal", json={
            "points": [{"lat": 42.45, "lng": 18.53}],
            "radius_m": 50.0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_cells_revealed"] > 0

    async def test_fog_status_endpoint(self, client):
        """GET /api/fog/status should return fog state."""
        resp = await client.get("/api/fog/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "state" in data

    async def test_fog_region_endpoint(self, client):
        """POST /api/fog/region should return region stats."""
        # First reveal some fog
        await client.post("/api/fog/reveal", json={
            "points": [{"lat": 42.45, "lng": 18.53}],
            "radius_m": 100.0,
        })
        resp = await client.post("/api/fog/region", json={
            "lat": 42.45, "lng": 18.53, "radius_km": 1.0, "region_name": "test",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["region_name"] == "test"

    async def test_fog_cells_endpoint(self, client):
        """GET /api/fog/cells should return explored cells."""
        await client.post("/api/fog/reveal", json={
            "points": [{"lat": 42.45, "lng": 18.53}],
            "radius_m": 50.0,
        })
        resp = await client.get("/api/fog/cells", params={
            "lat": 42.45, "lng": 18.53, "radius_km": 1.0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0

    async def test_achievements_endpoint(self, client):
        """GET /api/achievements should return all achievements."""
        resp = await client.get("/api/achievements")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_available"] == len(gam_service.ACHIEVEMENTS)

    async def test_profile_endpoint(self, client):
        """GET /api/explorer/profile should return profile."""
        resp = await client.get("/api/explorer/profile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["profile"]["level"] == "novice"
        assert data["profile"]["total_xp"] == 0

    async def test_xp_history_endpoint(self, client):
        """GET /api/explorer/xp-history should return events."""
        resp = await client.get("/api/explorer/xp-history")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert "total" in data

    async def test_leaderboard_endpoint(self, client):
        """GET /api/explorer/leaderboard should return leaderboard."""
        resp = await client.get("/api/explorer/leaderboard")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) >= 1
        assert data["your_rank"] == 1

    async def test_fog_cells_invalid_coords(self, client):
        """GET /api/fog/cells with invalid coords should return 422."""
        resp = await client.get("/api/fog/cells", params={
            "lat": 100, "lng": 18.53, "radius_km": 5.0,
        })
        assert resp.status_code == 422

    async def test_fog_cells_invalid_radius(self, client):
        """GET /api/fog/cells with out-of-range radius should return 422."""
        resp = await client.get("/api/fog/cells", params={
            "lat": 42.45, "lng": 18.53, "radius_km": 100.0,
        })
        assert resp.status_code == 422


# ── Persistence Tests ────────────────────────────────────────────


class TestPersistence:
    """Tests for data persistence."""

    async def test_fog_persistence(self):
        """Fog data should be saved and reloadable."""
        await gam_service.reveal_fog(
            [(42.45, 18.53, datetime.now(UTC))], radius_m=50.0
        )
        data_dir = gam_service._get_data_dir()
        assert (data_dir / "fog_cells.json").exists()

    async def test_achievements_persistence(self, sample_visits, sample_notes):
        """Achievement data should be saved."""
        await gam_service.check_achievements(
            visits=sample_visits[:1], notes=sample_notes[:1]
        )
        data_dir = gam_service._get_data_dir()
        assert (data_dir / "achievements.json").exists()

    async def test_xp_persistence(self, sample_visits):
        """XP data should be saved."""
        await gam_service.award_visit_xp(
            visit=sample_visits[0], notes=[]
        )
        data_dir = gam_service._get_data_dir()
        assert (data_dir / "xp_events.json").exists()
