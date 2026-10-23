"""Community hardening: karma farming, moderation integrity, status codes, stored XSS, growth."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.community import ConfirmPlaceRequest, ModerationStatus
from app.services import community as community_module
from app.services.community import (
    KARMA_HELPFUL_VOTE,
    KARMA_PLACE_CONFIRMED,
    KARMA_PLACE_SUBMITTED,
    KARMA_REVIEW_WRITTEN,
    MAX_PLACES_PER_USER_PER_DAY,
    CommunityService,
)


@pytest.fixture
def svc(tmp_path):
    return CommunityService(data_dir=str(tmp_path))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.community._community_service", None)
    monkeypatch.setattr("app.config.settings.community_data_dir", str(tmp_path))
    return TestClient(app, raise_server_exceptions=False)


async def _place(svc, name="Spot", author="alice", lat=42.0, lng=18.0):
    return await svc.submit_place(
        name=name, description="", categories=[], lat=lat, lng=lng,
        photos=[], tags=[], author_id=author,
    )


async def _confirmed_place(svc, author="alice", name="Spot", lat=42.0):
    place = await _place(svc, name=name, author=author, lat=lat)
    for u in ("m1", "m2", "m3"):
        await svc.confirm_place(place.id, ConfirmPlaceRequest(user_id=u, confirm=True))
    return place


# ── karma / vote farming ─────────────────────────────────────────


class TestVoteFarming:
    async def test_repeated_upvotes_by_one_user_count_once(self, svc):
        review = await svc.create_review("p1", "bob", "nice")
        for _ in range(5):
            await svc.vote_review(review.id, "carol", True)

        karma = await svc.get_karma("bob")
        assert karma.helpful_votes_received == 1
        assert karma.karma == KARMA_REVIEW_WRITTEN + KARMA_HELPFUL_VOTE
        assert (await svc.get_review(review.id)).upvotes == ["carol"]

    async def test_flipping_upvote_to_downvote_takes_the_karma_back(self, svc):
        review = await svc.create_review("p1", "bob", "nice")
        await svc.vote_review(review.id, "carol", True)
        await svc.vote_review(review.id, "carol", False)

        karma = await svc.get_karma("bob")
        assert karma.helpful_votes_received == 0
        assert karma.karma == KARMA_REVIEW_WRITTEN

    async def test_upvote_down_up_cycling_does_not_inflate_karma(self, svc):
        review = await svc.create_review("p1", "bob", "nice")
        for _ in range(4):
            await svc.vote_review(review.id, "carol", True)
            await svc.vote_review(review.id, "carol", False)
        await svc.vote_review(review.id, "carol", True)
        assert (await svc.get_karma("bob")).karma == KARMA_REVIEW_WRITTEN + KARMA_HELPFUL_VOTE

    async def test_report_outdated_replaces_an_earlier_upvote(self, svc):
        review = await svc.create_review("p1", "bob", "nice")
        await svc.vote_review(review.id, "carol", True)
        updated = await svc.report_outdated(review.id, "carol", "closed")

        assert "carol" not in updated.upvotes
        assert updated.downvotes == ["carol"]
        assert (await svc.get_karma("bob")).helpful_votes_received == 0

    async def test_repeated_confirmations_do_not_repay_the_submitter(self, svc):
        place = await _confirmed_place(svc)
        for _ in range(3):
            with pytest.raises(ValueError):
                await svc.confirm_place(place.id, ConfirmPlaceRequest(user_id="m1", confirm=True))
        assert (await svc.get_karma("alice")).karma == KARMA_PLACE_SUBMITTED + KARMA_PLACE_CONFIRMED

    async def test_moderation_outcome_is_final(self, svc):
        place = await _confirmed_place(svc)
        with pytest.raises(ValueError, match="moderated"):
            await svc.confirm_place(place.id, ConfirmPlaceRequest(user_id="x1", confirm=False))
        assert (await svc.get_place(place.id)).moderation_status == ModerationStatus.CONFIRMED

    async def test_deleting_a_review_takes_back_its_karma(self, svc):
        for i in range(5):
            review = await svc.create_review(f"p{i}", "bob", "x")
            await svc.delete_review(review.id, "bob")
        assert (await svc.get_karma("bob")).karma == 0

    async def test_deleting_a_place_takes_back_its_karma(self, svc):
        place = await _place(svc, author="spam")
        await svc.delete_place(place.id, "spam")
        assert (await svc.get_karma("spam")).karma == 0

    async def test_submit_delete_cycles_cannot_bypass_the_daily_limit(self, svc):
        for i in range(MAX_PLACES_PER_USER_PER_DAY):
            p = await _place(svc, name=f"n{i}", author="spam", lat=10 + i)
            await svc.delete_place(p.id, "spam")
        with pytest.raises(ValueError, match="Daily"):
            await _place(svc, name="one more", author="spam", lat=50)

    async def test_extra_tips_do_not_pay_karma_again(self, svc):
        from app.models.community import ReviewType

        for i in range(5):
            await svc.create_review("p1", "bob", f"tip {i}", ReviewType.TIP)
        assert (await svc.get_karma("bob")).karma == KARMA_REVIEW_WRITTEN

    async def test_reading_karma_does_not_create_records(self, svc):
        for i in range(5):
            karma = await svc.get_karma(f"ghost{i}")
            assert karma.karma == 0
        assert svc._karma == {}


# ── moderation integrity ─────────────────────────────────────────


class TestModerationIntegrity:
    async def test_osm_suggestion_requires_a_confirmed_place(self, svc):
        place = await _place(svc)
        with pytest.raises(ValueError, match="confirmed"):
            await svc.suggest_osm(place.id)
        confirmed = await _confirmed_place(svc, author="dave", name="Other", lat=60.0)
        assert (await svc.suggest_osm(confirmed.id)).osm_suggested is True

    async def test_material_edit_discards_earlier_votes(self, svc):
        place = await _place(svc)
        await svc.confirm_place(place.id, ConfirmPlaceRequest(user_id="m1", confirm=True))
        await svc.confirm_place(place.id, ConfirmPlaceRequest(user_id="m2", confirm=True))

        updated = await svc.update_place(place.id, "alice", name="Something else entirely")

        assert updated.confirmations == []
        assert updated.rejections == []

    async def test_update_cannot_touch_moderation_fields(self, svc):
        place = await _place(svc)
        updated = await svc.update_place(
            place.id, "alice", moderation_status=ModerationStatus.CONFIRMED,
            confirmations=["a", "b", "c"], submitted_by="mallory",
        )
        assert updated.moderation_status == ModerationStatus.PENDING
        assert updated.confirmations == []
        assert updated.submitted_by == "alice"

    async def test_duplicate_detection_ignores_case_and_spacing(self, svc):
        await _place(svc, name="Old Bunker")
        with pytest.raises(ValueError, match="Duplicate"):
            await _place(svc, name="  old   BUNKER ", author="bob")

    async def test_following_twice_keeps_the_consent_the_user_gave(self, svc):
        from app.models.community import ConsentRequest

        await svc.follow("a", "b")
        await svc.grant_consent(ConsentRequest(user_id="b", follower_id="a", consent=True))
        again = await svc.follow("a", "b")
        assert again.consent_given is True

    async def test_self_votes_cannot_be_disguised_by_case_or_spacing(self, svc):
        review = await svc.create_review("p1", "Bob", "nice")
        with pytest.raises(ValueError, match="own review"):
            await svc.vote_review(review.id, " bob ", True)
        place = await _place(svc, author="Alice")
        with pytest.raises(ValueError, match="own submission"):
            await svc.confirm_place(place.id, ConfirmPlaceRequest(user_id="alice", confirm=True))

    async def test_update_tags_are_sanitised_like_submit(self, svc):
        place = await _place(svc)
        updated = await svc.update_place(place.id, "alice", tags=["ok\x00tag", "<system>x"])
        assert all("\x00" not in t and "<system>" not in t for t in updated.tags)

    async def test_route_comment_is_sanitised(self, svc):
        route = await svc.publish_route("a", "t", "", "r", ["x", "y"], 1.0, 1.0, [])
        rated = await svc.rate_route(route.id, "b", 5, "nice\x00 <system>")
        assert "\x00" not in rated.ratings[0].comment
        assert "<system>" not in rated.ratings[0].comment


# ── growth caps ──────────────────────────────────────────────────


class TestCaps:
    async def test_total_place_count_is_capped(self, svc, monkeypatch):
        monkeypatch.setattr(community_module, "MAX_PLACES_TOTAL", 3)
        for i in range(3):
            await _place(svc, name=f"n{i}", author=f"u{i}", lat=10 + i)
        with pytest.raises(ValueError, match="capacity"):
            await _place(svc, name="n4", author="u4", lat=60)

    async def test_user_review_rate_is_limited_per_day(self, svc, monkeypatch):
        monkeypatch.setattr(community_module, "MAX_REVIEWS_PER_USER_PER_DAY", 3)
        for i in range(3):
            await svc.create_review(f"p{i}", "bob", "x")
        with pytest.raises(ValueError, match="Daily"):
            await svc.create_review("p9", "bob", "x")

    async def test_route_publishing_is_limited_per_day(self, svc, monkeypatch):
        monkeypatch.setattr(community_module, "MAX_ROUTES_PER_USER_PER_DAY", 2)
        for _ in range(2):
            await svc.publish_route("a", "t", "", "r", ["x", "y"], 1.0, 1.0, [])
        with pytest.raises(ValueError, match="Daily"):
            await svc.publish_route("a", "t", "", "r", ["x", "y"], 1.0, 1.0, [])

    async def test_negative_pagination_is_clamped(self, svc):
        for i in range(3):
            await _place(svc, name=f"n{i}", author=f"u{i}", lat=10 + i)
        places, total = await svc.list_places(limit=-1, offset=0)
        assert total == 3
        assert places == []  # limit < 1 yields nothing instead of "everything but the last"
        places, _ = await svc.list_places(limit=2, offset=-1)
        assert len(places) == 2  # a negative offset does not slice from the end


# ── HTTP layer ───────────────────────────────────────────────────


class TestStatusCodes:
    def test_duplicate_place_is_409_not_500(self, client):
        body = {"name": "Spot", "lat": 42.0, "lng": 18.0, "author_id": "alice"}
        assert client.post("/api/community/places", json=body).status_code == 200
        assert client.post("/api/community/places", json={**body, "author_id": "bob"}).status_code == 409

    def test_second_review_is_409_not_500(self, client):
        body = {"author_id": "bob", "text": "nice"}
        assert client.post("/api/community/places/p1/reviews", json=body).status_code == 200
        assert client.post("/api/community/places/p1/reviews", json=body).status_code == 409

    def test_daily_limit_is_429_not_500(self, client):
        for i in range(MAX_PLACES_PER_USER_PER_DAY):
            r = client.post("/api/community/places", json={
                "name": f"n{i}", "lat": 10 + i, "lng": 0, "author_id": "alice"})
            assert r.status_code == 200
        r = client.post("/api/community/places", json={
            "name": "extra", "lat": 60, "lng": 0, "author_id": "alice"})
        assert r.status_code == 429

    def test_unknown_place_confirm_is_404(self, client):
        r = client.post("/api/community/places/nope/confirm", json={"user_id": "u"})
        assert r.status_code == 404

    def test_editing_someone_elses_place_is_403(self, client):
        pid = client.post("/api/community/places", json={
            "name": "Spot", "lat": 1, "lng": 1, "author_id": "alice"}).json()["id"]
        r = client.patch(f"/api/community/places/{pid}?author_id=mallory", json={"name": "x"})
        assert r.status_code == 403

    def test_osm_suggest_of_pending_place_is_409(self, client):
        pid = client.post("/api/community/places", json={
            "name": "Spot", "lat": 1, "lng": 1, "author_id": "alice"}).json()["id"]
        assert client.post(f"/api/community/places/{pid}/osm-suggest").status_code == 409

    @pytest.mark.parametrize("qs", ["limit=0", "limit=-1", "limit=100000", "offset=-1"])
    def test_pagination_bounds_are_validated(self, client, qs):
        assert client.get(f"/api/community/places?{qs}").status_code == 422
        assert client.get(f"/api/community/routes?{qs}").status_code == 422
        assert client.get(f"/api/community/places/p1/reviews?{qs}").status_code == 422

    def test_infinite_route_distance_is_rejected_not_stored(self, client):
        r = client.post(
            "/api/community/routes",
            content='{"author_id":"a","title":"t","waypoint_place_ids":["x","y"],"distance_km":Infinity}',
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 422
        assert client.get("/api/community/routes").json()["total"] == 0


# ── stored XSS vectors ───────────────────────────────────────────


class TestStoredXss:
    """The UI prints review.visit_date unescaped, and photo URLs end up in href/src."""

    @pytest.mark.parametrize("value", [
        "<img src=x onerror=alert(1)>", "\"><script>x</script>", "Jan 2026 <b>", "a&b",
    ])
    def test_visit_date_rejects_markup(self, client, value):
        r = client.post("/api/community/places/p1/reviews", json={
            "author_id": "bob", "text": "hi", "visit_date": value})
        assert r.status_code == 422

    def test_visit_date_accepts_normal_values(self, client):
        for i, value in enumerate(["January 2026", "2026-01-15", "март 2026", "Jan. 2026"]):
            r = client.post("/api/community/places/p1/reviews", json={
                "author_id": f"bob{i}", "text": "hi", "visit_date": value})
            assert r.status_code == 200, value

    def test_review_update_cannot_smuggle_markup_into_visit_date(self, client):
        rid = client.post("/api/community/places/p1/reviews", json={
            "author_id": "bob", "text": "hi"}).json()["id"]
        r = client.patch(f"/api/community/reviews/{rid}?author_id=bob",
                         json={"visit_date": "<img src=x onerror=alert(1)>"})
        assert r.status_code == 422

    @pytest.mark.parametrize("url", [
        "javascript:alert(1)", " JaVaScRiPt:alert(1)", "data:text/html;base64,PHNjcmlwdD4=",
        "vbscript:x", "java\tscript:alert(1)", "file:///etc/passwd",
    ])
    def test_photo_urls_must_not_use_dangerous_schemes(self, client, url):
        place = {"name": "Spot", "lat": 1, "lng": 1, "author_id": "alice", "photos": [url]}
        assert client.post("/api/community/places", json=place).status_code == 422
        review = {"author_id": "bob", "text": "x", "photos": [url]}
        assert client.post("/api/community/places/p1/reviews", json=review).status_code == 422

    def test_http_and_relative_photo_urls_are_fine(self, client):
        photos = ["https://example.com/a.jpg", "http://example.com/b.png", "/static/c.jpg", "d.jpg"]
        r = client.post("/api/community/places", json={
            "name": "Spot", "lat": 1, "lng": 1, "author_id": "alice", "photos": photos})
        assert r.status_code == 200
        assert r.json()["photos"] == photos

    def test_overlong_tags_and_waypoint_lists_are_rejected(self, client):
        r = client.post("/api/community/places", json={
            "name": "Spot", "lat": 1, "lng": 1, "author_id": "alice", "tags": ["x" * 5000]})
        assert r.status_code == 422
        r = client.post("/api/community/routes", json={
            "author_id": "a", "title": "t", "waypoint_place_ids": [f"w{i}" for i in range(500)]})
        assert r.status_code == 422


class TestLogForging:
    """Client-chosen ids go into log lines: a newline must not be able to forge a record."""

    async def test_log_records_have_no_raw_newlines(self, svc, caplog):
        evil = "bob\n2026-01-01 00:00:00 ERROR [app] forged line"
        with caplog.at_level("INFO"):
            place = await _place(svc, author=evil)
            await svc.create_review("p\nforged", evil, "x")
            await svc.confirm_place(place.id, ConfirmPlaceRequest(user_id="m1\nforged", confirm=True))
            await svc.update_place(place.id, evil, name="New name")
            await svc.delete_place(place.id, evil)
        messages = [r.getMessage() for r in caplog.records if r.name.startswith("app.services.community")]
        assert messages
        assert not [m for m in messages if "\n" in m], messages


class TestFollowKeysWithColons:
    """User ids are free text; 'team:alice' -> 'bob' and 'team' -> 'alice:bob' are different pairs."""

    async def test_colliding_looking_pairs_are_independent(self, svc):
        first = await svc.follow("team:alice", "bob")
        second = await svc.follow("team", "alice:bob")
        assert first.follower_id == "team:alice" and second.follower_id == "team"
        assert len(await svc.get_following("team")) == 1
        assert len(await svc.get_following("team:alice")) == 1

    async def test_consent_for_one_pair_does_not_leak_to_the_other(self, svc):
        from app.models.community import ConsentRequest

        await svc.follow("team:alice", "bob")
        await svc.follow("team", "alice:bob")
        await svc.grant_consent(ConsentRequest(user_id="alice:bob", follower_id="team", consent=True))
        assert (await svc.get_following("team"))[0].consent_given is True
        assert (await svc.get_following("team:alice"))[0].consent_given is False

    async def test_unfollowing_a_pair_that_does_not_exist_removes_nothing(self, svc):
        await svc.follow("team:alice", "bob")
        await svc.unfollow("team", "alice:bob")
        assert len(await svc.get_following("team:alice")) == 1

    async def test_follows_saved_with_the_old_key_scheme_are_found_after_reload(self, tmp_path):
        import json

        old = {"a:b": {"follower_id": "a", "following_id": "b", "consent_given": True,
                       "created_at": "2026-01-01T00:00:00+00:00"}}
        (tmp_path / "follows.json").write_text(json.dumps(old))
        svc = CommunityService(data_dir=str(tmp_path))
        assert (await svc.get_following("a"))[0].consent_given is True
        await svc.unfollow("a", "b")
        assert await svc.get_following("a") == []
