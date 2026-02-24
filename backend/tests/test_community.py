"""Tests for Community feature (Epic 8)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.models.community import (
    CommunityPlace,
    ConfirmPlaceRequest,
    ConsentRequest,
    ContributorKarma,
    ExplorerFollow,
    ModerationStatus,
    PublishedRoute,
    Review,
    ReviewType,
)
from app.services.community import (
    KARMA_PLACE_CONFIRMED,
    KARMA_PLACE_SUBMITTED,
    KARMA_REVIEW_WRITTEN,
    KARMA_ROUTE_PUBLISHED,
    MAX_PLACES_PER_USER_PER_DAY,
    CommunityService,
)


@pytest.fixture
def community_service(tmp_path):
    """Create a CommunityService with a temporary data directory."""
    return CommunityService(data_dir=str(tmp_path))


# ── Story 8.1: User-Submitted Places ────────────────────────────


class TestSubmitPlace:
    @pytest.mark.asyncio
    async def test_submit_place(self, community_service):
        place = await community_service.submit_place(
            name="Hidden Bunker",
            description="An abandoned bunker in the forest",
            categories=["military"],
            lat=42.45,
            lng=18.53,
            photos=["photo1.jpg"],
            tags=["bunker", "ww2"],
            author_id="user1",
        )
        assert place.name == "Hidden Bunker"
        assert place.moderation_status == ModerationStatus.PENDING
        assert place.submitted_by == "user1"
        assert place.id.startswith("community_")

    @pytest.mark.asyncio
    async def test_submit_awards_karma(self, community_service):
        await community_service.submit_place(
            name="Test Place", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="user1",
        )
        karma = await community_service.get_karma("user1")
        assert karma.karma == KARMA_PLACE_SUBMITTED
        assert karma.places_submitted == 1

    @pytest.mark.asyncio
    async def test_list_places_filter_by_status(self, community_service):
        await community_service.submit_place(
            name="Place A", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="user1",
        )
        places, total = await community_service.list_places(status=ModerationStatus.PENDING)
        assert total == 1
        assert places[0].name == "Place A"

        places, total = await community_service.list_places(status=ModerationStatus.CONFIRMED)
        assert total == 0


class TestModerationFlow:
    @pytest.mark.asyncio
    async def test_confirm_place_requires_threshold(self, community_service):
        place = await community_service.submit_place(
            name="Test", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="submitter",
        )
        # 2 confirmations not enough (threshold=3)
        await community_service.confirm_place(place.id, ConfirmPlaceRequest(user_id="mod1", confirm=True))
        await community_service.confirm_place(place.id, ConfirmPlaceRequest(user_id="mod2", confirm=True))
        p = await community_service.get_place(place.id)
        assert p.moderation_status == ModerationStatus.PENDING

        # 3rd confirmation triggers approval
        await community_service.confirm_place(place.id, ConfirmPlaceRequest(user_id="mod3", confirm=True))
        p = await community_service.get_place(place.id)
        assert p.moderation_status == ModerationStatus.CONFIRMED

    @pytest.mark.asyncio
    async def test_cannot_confirm_own_place(self, community_service):
        place = await community_service.submit_place(
            name="Test", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="user1",
        )
        with pytest.raises(ValueError, match="Cannot confirm your own"):
            await community_service.confirm_place(place.id, ConfirmPlaceRequest(user_id="user1", confirm=True))

    @pytest.mark.asyncio
    async def test_rejection_flow(self, community_service):
        place = await community_service.submit_place(
            name="Spam", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="spammer",
        )
        for i in range(3):
            await community_service.confirm_place(place.id, ConfirmPlaceRequest(user_id=f"mod{i}", confirm=False))
        p = await community_service.get_place(place.id)
        assert p.moderation_status == ModerationStatus.REJECTED

    @pytest.mark.asyncio
    async def test_confirmed_place_awards_submitter_karma(self, community_service):
        place = await community_service.submit_place(
            name="Good Place", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="submitter",
        )
        for i in range(3):
            await community_service.confirm_place(place.id, ConfirmPlaceRequest(user_id=f"mod{i}", confirm=True))
        karma = await community_service.get_karma("submitter")
        assert karma.karma == KARMA_PLACE_SUBMITTED + KARMA_PLACE_CONFIRMED
        assert karma.places_confirmed == 1


class TestOSMIntegration:
    @pytest.mark.asyncio
    async def test_suggest_osm(self, community_service):
        place = await community_service.submit_place(
            name="Test", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="user1",
        )
        updated = await community_service.suggest_osm(place.id)
        assert updated.osm_suggested is True


# ── Story 8.2: Reviews & Tips ────────────────────────────────────


class TestReviews:
    @pytest.mark.asyncio
    async def test_create_review(self, community_service):
        review = await community_service.create_review(
            place_id="place1",
            author_id="user1",
            text="Great hidden spot!",
            review_type=ReviewType.REVIEW,
            visit_date="January 2026",
        )
        assert review.text == "Great hidden spot!"
        assert review.visit_date == "January 2026"
        assert review.id.startswith("review_")

    @pytest.mark.asyncio
    async def test_create_tip(self, community_service):
        tip = await community_service.create_review(
            place_id="place1",
            author_id="user1",
            text="Enter from the back",
            review_type=ReviewType.TIP,
        )
        assert tip.review_type == ReviewType.TIP

    @pytest.mark.asyncio
    async def test_review_awards_karma(self, community_service):
        await community_service.create_review(
            place_id="place1", author_id="user1", text="Good", review_type=ReviewType.REVIEW,
        )
        karma = await community_service.get_karma("user1")
        assert karma.karma == KARMA_REVIEW_WRITTEN
        assert karma.reviews_written == 1


class TestVoting:
    @pytest.mark.asyncio
    async def test_upvote(self, community_service):
        review = await community_service.create_review(
            place_id="place1", author_id="author", text="Nice",
        )
        updated = await community_service.vote_review(review.id, "voter1", upvote=True)
        assert "voter1" in updated.upvotes

    @pytest.mark.asyncio
    async def test_downvote(self, community_service):
        review = await community_service.create_review(
            place_id="place1", author_id="author", text="Nice",
        )
        updated = await community_service.vote_review(review.id, "voter1", upvote=False)
        assert "voter1" in updated.downvotes

    @pytest.mark.asyncio
    async def test_cannot_vote_own_review(self, community_service):
        review = await community_service.create_review(
            place_id="place1", author_id="author", text="Nice",
        )
        with pytest.raises(ValueError, match="Cannot vote on your own"):
            await community_service.vote_review(review.id, "author", upvote=True)

    @pytest.mark.asyncio
    async def test_vote_replaces_previous(self, community_service):
        review = await community_service.create_review(
            place_id="place1", author_id="author", text="Nice",
        )
        await community_service.vote_review(review.id, "voter1", upvote=True)
        updated = await community_service.vote_review(review.id, "voter1", upvote=False)
        assert "voter1" not in updated.upvotes
        assert "voter1" in updated.downvotes

    @pytest.mark.asyncio
    async def test_reviews_sorted_by_score(self, community_service):
        r1 = await community_service.create_review(
            place_id="place1", author_id="a1", text="OK",
        )
        r2 = await community_service.create_review(
            place_id="place1", author_id="a2", text="Great!",
        )
        # r2 gets upvoted
        await community_service.vote_review(r2.id, "voter1", upvote=True)
        await community_service.vote_review(r2.id, "voter2", upvote=True)

        reviews, total = await community_service.list_reviews("place1", sort_by="score")
        assert total == 2
        assert reviews[0].id == r2.id  # highest score first

    @pytest.mark.asyncio
    async def test_filter_reviews_by_type(self, community_service):
        await community_service.create_review(
            place_id="place1", author_id="a1", text="Review", review_type=ReviewType.REVIEW,
        )
        await community_service.create_review(
            place_id="place1", author_id="a2", text="Tip", review_type=ReviewType.TIP,
        )
        reviews, total = await community_service.list_reviews("place1", review_type=ReviewType.TIP)
        assert total == 1
        assert reviews[0].review_type == ReviewType.TIP


# ── Story 8.3: Social Routes ────────────────────────────────────


class TestPublishedRoutes:
    @pytest.mark.asyncio
    async def test_publish_route(self, community_service):
        route = await community_service.publish_route(
            author_id="user1",
            title="Morning Urbex Walk",
            description="Explore abandoned factories",
            region="Berlin",
            waypoint_place_ids=["p1", "p2", "p3"],
            distance_km=5.2,
            duration_hours=2.5,
            tags=["urbex", "abandoned"],
        )
        assert route.title == "Morning Urbex Walk"
        assert route.region == "Berlin"
        assert len(route.waypoint_place_ids) == 3

    @pytest.mark.asyncio
    async def test_publish_awards_karma(self, community_service):
        await community_service.publish_route(
            author_id="user1", title="Test", description="", region="",
            waypoint_place_ids=["p1", "p2"], distance_km=0, duration_hours=0, tags=[],
        )
        karma = await community_service.get_karma("user1")
        assert karma.karma == KARMA_ROUTE_PUBLISHED
        assert karma.routes_published == 1

    @pytest.mark.asyncio
    async def test_rate_route(self, community_service):
        route = await community_service.publish_route(
            author_id="author", title="Test", description="", region="",
            waypoint_place_ids=["p1", "p2"], distance_km=0, duration_hours=0, tags=[],
        )
        updated = await community_service.rate_route(route.id, "rater1", 5, "Excellent!")
        assert updated.average_rating == 5.0

    @pytest.mark.asyncio
    async def test_cannot_rate_own_route(self, community_service):
        route = await community_service.publish_route(
            author_id="author", title="Test", description="", region="",
            waypoint_place_ids=["p1", "p2"], distance_km=0, duration_hours=0, tags=[],
        )
        with pytest.raises(ValueError, match="Cannot rate your own"):
            await community_service.rate_route(route.id, "author", 5)

    @pytest.mark.asyncio
    async def test_top_routes_by_region(self, community_service):
        await community_service.publish_route(
            author_id="u1", title="Berlin Walk", description="", region="Berlin",
            waypoint_place_ids=["p1", "p2"], distance_km=0, duration_hours=0, tags=[],
        )
        await community_service.publish_route(
            author_id="u2", title="Prague Tour", description="", region="Prague",
            waypoint_place_ids=["p3", "p4"], distance_km=0, duration_hours=0, tags=[],
        )
        routes, total = await community_service.list_routes(region="berlin")
        assert total == 1
        assert routes[0].title == "Berlin Walk"


class TestFollowSystem:
    @pytest.mark.asyncio
    async def test_follow_requires_consent(self, community_service):
        follow = await community_service.follow("follower", "explorer")
        assert follow.consent_given is False

    @pytest.mark.asyncio
    async def test_grant_consent(self, community_service):
        await community_service.follow("follower", "explorer")
        follow = await community_service.grant_consent(
            ConsentRequest(user_id="explorer", follower_id="follower", consent=True)
        )
        assert follow.consent_given is True

    @pytest.mark.asyncio
    async def test_revoke_consent(self, community_service):
        await community_service.follow("follower", "explorer")
        await community_service.grant_consent(
            ConsentRequest(user_id="explorer", follower_id="follower", consent=True)
        )
        follow = await community_service.grant_consent(
            ConsentRequest(user_id="explorer", follower_id="follower", consent=False)
        )
        assert follow.consent_given is False

    @pytest.mark.asyncio
    async def test_cannot_follow_self(self, community_service):
        with pytest.raises(ValueError, match="Cannot follow yourself"):
            await community_service.follow("user1", "user1")

    @pytest.mark.asyncio
    async def test_unfollow(self, community_service):
        await community_service.follow("follower", "explorer")
        await community_service.unfollow("follower", "explorer")
        followers = await community_service.get_followers("explorer")
        assert len(followers) == 0

    @pytest.mark.asyncio
    async def test_get_followers_and_following(self, community_service):
        await community_service.follow("f1", "explorer")
        await community_service.follow("f2", "explorer")
        await community_service.follow("explorer", "other")

        followers = await community_service.get_followers("explorer")
        assert len(followers) == 2

        following = await community_service.get_following("explorer")
        assert len(following) == 1


# ── Persistence ──────────────────────────────────────────────────


class TestPersistence:
    @pytest.mark.asyncio
    async def test_data_persists_across_instances(self, tmp_path):
        svc1 = CommunityService(data_dir=str(tmp_path))
        await svc1.submit_place(
            name="Persisted Place", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="user1",
        )

        # New service instance loads from same directory
        svc2 = CommunityService(data_dir=str(tmp_path))
        places, total = await svc2.list_places()
        assert total == 1
        assert places[0].name == "Persisted Place"


# ── Karma Accumulation ───────────────────────────────────────────


class TestKarmaAccumulation:
    @pytest.mark.asyncio
    async def test_multi_action_karma(self, community_service):
        """Karma accumulates from multiple different actions."""
        # Submit a place (+5)
        await community_service.submit_place(
            name="Place", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="user1",
        )
        # Write a review (+3)
        await community_service.create_review(
            place_id="place1", author_id="user1", text="Good",
        )
        # Publish a route (+5)
        await community_service.publish_route(
            author_id="user1", title="Route", description="", region="",
            waypoint_place_ids=["p1", "p2"], distance_km=0, duration_hours=0, tags=[],
        )

        karma = await community_service.get_karma("user1")
        assert karma.karma == KARMA_PLACE_SUBMITTED + KARMA_REVIEW_WRITTEN + KARMA_ROUTE_PUBLISHED
        assert karma.places_submitted == 1
        assert karma.reviews_written == 1
        assert karma.routes_published == 1


# ── API Integration Tests ────────────────────────────────────────


class TestCommunityAPI:
    """Tests for the API endpoints via FastAPI test client."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        """Create test client with isolated data directory."""
        monkeypatch.setattr("app.services.community._community_service", None)
        monkeypatch.setattr("app.config.settings.community_data_dir", str(tmp_path))
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_submit_place_api(self, client):
        resp = client.post("/api/community/places", json={
            "name": "API Test Place",
            "description": "From API",
            "categories": ["abandoned"],
            "lat": 42.45,
            "lng": 18.53,
            "photos": [],
            "tags": ["test"],
            "author_id": "api_user",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "API Test Place"
        assert data["moderation_status"] == "pending"

    def test_list_places_api(self, client):
        client.post("/api/community/places", json={
            "name": "P1", "lat": 0, "lng": 0, "author_id": "u1",
        })
        resp = client.get("/api/community/places")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_create_review_api(self, client):
        resp = client.post("/api/community/places/place1/reviews", json={
            "author_id": "reviewer",
            "review_type": "tip",
            "text": "Enter from back door",
            "visit_date": "February 2026",
        })
        assert resp.status_code == 200
        assert resp.json()["review_type"] == "tip"

    def test_publish_route_api(self, client):
        resp = client.post("/api/community/routes", json={
            "author_id": "user1",
            "title": "Test Route",
            "description": "A nice walk",
            "region": "Berlin",
            "waypoint_place_ids": ["p1", "p2"],
            "distance_km": 3.5,
            "duration_hours": 1.5,
            "tags": ["walk"],
        })
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test Route"

    def test_follow_api(self, client):
        resp = client.post("/api/community/follow", json={
            "follower_id": "fan",
            "following_id": "explorer",
        })
        assert resp.status_code == 200
        assert resp.json()["consent_given"] is False

    def test_karma_api(self, client):
        client.post("/api/community/places", json={
            "name": "P", "lat": 0, "lng": 0, "author_id": "u1",
        })
        resp = client.get("/api/community/karma/u1")
        assert resp.status_code == 200
        assert resp.json()["karma"]["karma"] == KARMA_PLACE_SUBMITTED

    def test_vote_api(self, client):
        # Create review first
        resp = client.post("/api/community/places/p1/reviews", json={
            "author_id": "author",
            "text": "Great spot",
        })
        review_id = resp.json()["id"]

        resp = client.post(f"/api/community/reviews/{review_id}/vote", json={
            "user_id": "voter",
            "upvote": True,
        })
        assert resp.status_code == 200
        assert "voter" in resp.json()["upvotes"]

    def test_get_place_not_found(self, client):
        resp = client.get("/api/community/places/nonexistent")
        assert resp.status_code == 404


# ── Iteration 2: Edge Cases & New Features ───────────────────────


class TestDuplicateDetection:
    @pytest.mark.asyncio
    async def test_duplicate_place_rejected(self, community_service):
        await community_service.submit_place(
            name="Old Mill", description="", categories=[], lat=42.45, lng=18.53,
            photos=[], tags=[], author_id="user1",
        )
        with pytest.raises(ValueError, match="Duplicate place"):
            await community_service.submit_place(
                name="Old Mill", description="Different desc", categories=[], lat=42.45, lng=18.53,
                photos=[], tags=[], author_id="user2",
            )

    @pytest.mark.asyncio
    async def test_same_name_different_location_allowed(self, community_service):
        await community_service.submit_place(
            name="Old Mill", description="", categories=[], lat=42.45, lng=18.53,
            photos=[], tags=[], author_id="user1",
        )
        # Far away location — should be allowed
        place = await community_service.submit_place(
            name="Old Mill", description="", categories=[], lat=50.00, lng=20.00,
            photos=[], tags=[], author_id="user2",
        )
        assert place.name == "Old Mill"


class TestDailySubmissionLimit:
    @pytest.mark.asyncio
    async def test_daily_limit(self, community_service):
        for i in range(MAX_PLACES_PER_USER_PER_DAY):
            await community_service.submit_place(
                name=f"Place {i}", description="", categories=[],
                lat=i * 0.1, lng=i * 0.1,
                photos=[], tags=[], author_id="spammer",
            )
        with pytest.raises(ValueError, match="Daily submission limit"):
            await community_service.submit_place(
                name="One more", description="", categories=[], lat=99, lng=99,
                photos=[], tags=[], author_id="spammer",
            )


class TestUpdateDelete:
    @pytest.mark.asyncio
    async def test_update_place(self, community_service):
        place = await community_service.submit_place(
            name="Old Name", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="author",
        )
        updated = await community_service.update_place(place.id, "author", name="New Name")
        assert updated.name == "New Name"

    @pytest.mark.asyncio
    async def test_cannot_update_others_place(self, community_service):
        place = await community_service.submit_place(
            name="Test", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="author",
        )
        with pytest.raises(ValueError, match="Only the author"):
            await community_service.update_place(place.id, "other", name="Hacked")

    @pytest.mark.asyncio
    async def test_cannot_update_moderated_place(self, community_service):
        place = await community_service.submit_place(
            name="Test", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="author",
        )
        for i in range(3):
            await community_service.confirm_place(place.id, ConfirmPlaceRequest(user_id=f"m{i}", confirm=True))
        with pytest.raises(ValueError, match="Cannot update"):
            await community_service.update_place(place.id, "author", name="Edit confirmed")

    @pytest.mark.asyncio
    async def test_delete_place(self, community_service):
        place = await community_service.submit_place(
            name="To Delete", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="author",
        )
        await community_service.delete_place(place.id, "author")
        assert await community_service.get_place(place.id) is None

    @pytest.mark.asyncio
    async def test_delete_place_removes_reviews(self, community_service):
        place = await community_service.submit_place(
            name="Place", description="", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="author",
        )
        await community_service.create_review(place_id=place.id, author_id="rev", text="Nice")
        await community_service.delete_place(place.id, "author")
        reviews, total = await community_service.list_reviews(place.id)
        assert total == 0

    @pytest.mark.asyncio
    async def test_update_review(self, community_service):
        review = await community_service.create_review(
            place_id="p1", author_id="author", text="Old text",
        )
        updated = await community_service.update_review(review.id, "author", text="New text")
        assert updated.text == "New text"

    @pytest.mark.asyncio
    async def test_delete_review(self, community_service):
        review = await community_service.create_review(
            place_id="p1", author_id="author", text="To delete",
        )
        await community_service.delete_review(review.id, "author")
        assert await community_service.get_review(review.id) is None

    @pytest.mark.asyncio
    async def test_cannot_delete_others_review(self, community_service):
        review = await community_service.create_review(
            place_id="p1", author_id="author", text="Mine",
        )
        with pytest.raises(ValueError, match="Only the author"):
            await community_service.delete_review(review.id, "other")


class TestReviewLimit:
    @pytest.mark.asyncio
    async def test_one_review_per_user_per_place(self, community_service):
        await community_service.create_review(
            place_id="p1", author_id="user1", text="First review", review_type=ReviewType.REVIEW,
        )
        with pytest.raises(ValueError, match="already have a review"):
            await community_service.create_review(
                place_id="p1", author_id="user1", text="Second review", review_type=ReviewType.REVIEW,
            )

    @pytest.mark.asyncio
    async def test_tips_unlimited(self, community_service):
        for i in range(5):
            await community_service.create_review(
                place_id="p1", author_id="user1", text=f"Tip {i}", review_type=ReviewType.TIP,
            )
        reviews, total = await community_service.list_reviews("p1", review_type=ReviewType.TIP)
        assert total == 5


class TestPlaceSummary:
    @pytest.mark.asyncio
    async def test_place_summary(self, community_service):
        await community_service.create_review(
            place_id="p1", author_id="a1", text="Great", review_type=ReviewType.REVIEW,
        )
        await community_service.create_review(
            place_id="p1", author_id="a2", text="Go early", review_type=ReviewType.TIP, visit_date="Feb 2026",
        )
        summary = await community_service.get_place_summary("p1")
        assert summary.review_count == 1
        assert summary.tip_count == 1
        assert summary.latest_visit_date == "Feb 2026"

    @pytest.mark.asyncio
    async def test_empty_place_summary(self, community_service):
        summary = await community_service.get_place_summary("nonexistent")
        assert summary.review_count == 0
        assert summary.tip_count == 0


class TestReportOutdated:
    @pytest.mark.asyncio
    async def test_report_outdated(self, community_service):
        review = await community_service.create_review(
            place_id="p1", author_id="author", text="Entrance open",
        )
        updated = await community_service.report_outdated(review.id, "reporter", "Entrance is now blocked")
        assert "reporter" in updated.downvotes


class TestSanitization:
    @pytest.mark.asyncio
    async def test_place_name_sanitized(self, community_service):
        place = await community_service.submit_place(
            name="Test\x00Place", description="Desc\x00ription", categories=[], lat=0, lng=0,
            photos=[], tags=[], author_id="user1",
        )
        assert "\x00" not in place.name
        assert "\x00" not in place.description

    @pytest.mark.asyncio
    async def test_review_text_sanitized(self, community_service):
        review = await community_service.create_review(
            place_id="p1", author_id="user1", text="Good\x00spot",
        )
        assert "\x00" not in review.text


class TestAPIIteration2:
    """Tests for new API endpoints from iteration 2."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.community._community_service", None)
        monkeypatch.setattr("app.config.settings.community_data_dir", str(tmp_path))
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_delete_place_api(self, client):
        resp = client.post("/api/community/places", json={
            "name": "To Delete", "lat": 0, "lng": 0, "author_id": "user1",
        })
        place_id = resp.json()["id"]
        resp = client.delete(f"/api/community/places/{place_id}?author_id=user1")
        assert resp.status_code == 200

    def test_update_place_api(self, client):
        resp = client.post("/api/community/places", json={
            "name": "Original", "lat": 0, "lng": 0, "author_id": "user1",
        })
        place_id = resp.json()["id"]
        resp = client.patch(f"/api/community/places/{place_id}?author_id=user1", json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_place_summary_api(self, client):
        client.post("/api/community/places/p1/reviews", json={
            "author_id": "a1", "text": "Nice", "review_type": "review",
        })
        resp = client.get("/api/community/places/p1/summary")
        assert resp.status_code == 200
        assert resp.json()["review_count"] == 1

    def test_delete_review_api(self, client):
        resp = client.post("/api/community/places/p1/reviews", json={
            "author_id": "author", "text": "To delete",
        })
        review_id = resp.json()["id"]
        resp = client.delete(f"/api/community/reviews/{review_id}?author_id=author")
        assert resp.status_code == 200

    def test_report_outdated_api(self, client):
        resp = client.post("/api/community/places/p1/reviews", json={
            "author_id": "author", "text": "Info",
        })
        review_id = resp.json()["id"]
        resp = client.post(f"/api/community/reviews/{review_id}/report-outdated", json={
            "user_id": "reporter", "reason": "Closed now",
        })
        assert resp.status_code == 200
