"""Community service (Epic 8) — User-generated content and social features.

Persistence: JSON files in data/community/ directory.
Thread safety: asyncio.Lock for concurrent writes.
Pattern: matches journal.py and gamification.py services.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.config import settings
from app.models.community import (
    CommunityPlace,
    ConfirmPlaceRequest,
    ConsentRequest,
    ContributorKarma,
    ExplorerFollow,
    ModerationStatus,
    PlaceSummary,
    PublishedRoute,
    Review,
    ReviewType,
    RouteRating,
)
from app.models.place import Coordinates
from app.utils.atomic_io import atomic_write_text, load_json_file, quarantine_corrupt
from app.utils.sanitize import sanitize_url, sanitize_user_input

logger = logging.getLogger(__name__)

# Moderation threshold: number of confirmations needed for a place to be accepted
MODERATION_CONFIRMATIONS_REQUIRED = 3

# Karma rewards
KARMA_PLACE_SUBMITTED = 5
KARMA_PLACE_CONFIRMED = 10  # when your submitted place gets confirmed
KARMA_REVIEW_WRITTEN = 3
KARMA_HELPFUL_VOTE = 1
KARMA_ROUTE_PUBLISHED = 5

# Per-user submission limits (per UTC day). Deleting content does not refund the quota.
MAX_PLACES_PER_USER_PER_DAY = 10
MAX_REVIEWS_PER_USER_PER_PLACE = 1
MAX_REVIEWS_PER_USER_PER_DAY = 50
MAX_ROUTES_PER_USER_PER_DAY = 10

# Global capacity (each save rewrites the whole store, so the files must stay small)
MAX_PLACES_TOTAL = 20_000
MAX_REVIEWS_TOTAL = 50_000
MAX_ROUTES_TOTAL = 10_000
MAX_FOLLOWS_TOTAL = 50_000

# Duplicate detection: places within this distance (degrees ~100m) are duplicates
DUPLICATE_DISTANCE_DEG = 0.001

# visit_date is a short free-text recency label ("January 2026"); the UI prints it as-is
_VISIT_DATE_RE = re.compile(r"^[\w .,/\-]{0,50}$")

_PLACE_EDITABLE = ("name", "description", "categories", "photos", "tags")
_REVIEW_EDITABLE = ("text", "photos", "visit_date")

_KARMA_COUNTERS = {
    "place_submitted": "places_submitted",
    "place_confirmed": "places_confirmed",
    "review_written": "reviews_written",
    "helpful_vote": "helpful_votes_received",
    "route_published": "routes_published",
}


class CommunityError(ValueError):
    """Base class: a request the service refuses (subclasses carry the HTTP meaning)."""


class NotFoundError(CommunityError):
    """The referenced object does not exist (404)."""


class ForbiddenError(CommunityError):
    """Not allowed for this user, e.g. editing someone else's content (403)."""


class ConflictError(CommunityError):
    """Conflicts with the current state: duplicate, already moderated (409)."""


class QuotaError(CommunityError):
    """A per-user daily quota is used up (429)."""


class CapacityError(CommunityError):
    """The global capacity of the store is reached (507)."""


def _same_user(a: str, b: str) -> bool:
    """User ids are client-chosen; do not let ' Alice' and 'alice' pass for different people."""
    return a.strip().casefold() == b.strip().casefold()


def _norm_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def _clean_photos(photos: list[str]) -> list[str]:
    clean: list[str] = []
    for url in photos:
        safe = sanitize_url(url)
        if safe is None:
            raise ValueError("photo URLs must be http(s) links or relative paths")
        clean.append(safe)
    return clean


def _clean_visit_date(value: str | None) -> str | None:
    if not value:
        return None
    value = sanitize_user_input(value)
    if not _VISIT_DATE_RE.match(value):
        raise ValueError("visit_date may only contain letters, digits, spaces and . , / -")
    return value


class CommunityService:
    """Manages community content: places, reviews, routes, follows, and karma."""

    def __init__(self, data_dir: str | None = None):
        self._data_dir = Path(data_dir or getattr(settings, "community_data_dir", "data/community"))
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._places: dict[str, dict] = {}
        self._reviews: dict[str, dict] = {}
        self._routes: dict[str, dict] = {}
        self._karma: dict[str, dict] = {}
        self._follows: dict[str, dict] = {}
        # Daily submission counters per user ({"date": ..., "place": n, "review": n, "route": n}).
        # Kept separately from the content so that deleting content does not refund the quota.
        self._quotas: dict[str, dict] = {}

        self._loaded = False
        self._write_lock = asyncio.Lock()

    # ── Persistence ──────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._places = self._load_store("places.json", CommunityPlace)
        self._reviews = self._load_store("reviews.json", Review)
        self._routes = self._load_store("routes.json", PublishedRoute)
        self._karma = self._load_store("karma.json", ContributorKarma)
        self._follows = self._load_store("follows.json", ExplorerFollow)
        quotas = load_json_file(self._data_dir / "quotas.json")
        today = self._today()
        if isinstance(quotas, dict):
            self._quotas = {
                uid: q for uid, q in quotas.items()
                if isinstance(q, dict) and q.get("date") == today
            }

    def _load_store(self, filename: str, model: type[BaseModel]) -> dict[str, dict]:
        """Load one store file. Records failing validation are skipped (one bad record must
        not 500 every listing) and the original file is backed up before it is rewritten."""
        path = self._data_dir / filename
        raw = load_json_file(path)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            quarantine_corrupt(path, "top-level JSON value is not an object")
            return {}
        good: dict[str, dict] = {}
        for key, value in raw.items():
            try:
                model.model_validate(value)
            except Exception:
                continue
            good[key] = value
        if len(good) != len(raw):
            quarantine_corrupt(path, f"{len(raw) - len(good)} record(s) failed validation")
        return good

    def _atomic_write(self, filename: str, data: dict) -> None:
        atomic_write_text(
            self._data_dir / filename,
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
        )

    def _save_places(self) -> None:
        self._atomic_write("places.json", self._places)

    def _save_reviews(self) -> None:
        self._atomic_write("reviews.json", self._reviews)

    def _save_routes(self) -> None:
        self._atomic_write("routes.json", self._routes)

    def _save_karma(self) -> None:
        self._atomic_write("karma.json", self._karma)

    def _save_follows(self) -> None:
        self._atomic_write("follows.json", self._follows)

    def _save_quotas(self) -> None:
        today = self._today()
        self._quotas = {u: q for u, q in self._quotas.items() if q.get("date") == today}
        self._atomic_write("quotas.json", self._quotas)

    # ── Quotas ───────────────────────────────────────────────────

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _quota_used(self, user_id: str, kind: str) -> int:
        q = self._quotas.get(user_id)
        if not q or q.get("date") != self._today():
            return 0
        return int(q.get(kind, 0))

    def _quota_bump(self, user_id: str, kind: str) -> None:
        today = self._today()
        q = self._quotas.get(user_id)
        if not q or q.get("date") != today:
            q = {"date": today}
            self._quotas[user_id] = q
        q[kind] = int(q.get(kind, 0)) + 1

    # ── Karma helpers ────────────────────────────────────────────

    def _get_or_create_karma(self, user_id: str) -> ContributorKarma:
        self._ensure_loaded()
        if user_id not in self._karma:
            karma = ContributorKarma(user_id=user_id)
            self._karma[user_id] = karma.model_dump(mode="json")
        return ContributorKarma(**self._karma[user_id])

    def _adjust_karma(self, user_id: str, amount: int, reason: str, sign: int) -> ContributorKarma:
        karma = self._get_or_create_karma(user_id)
        karma.karma = max(0, karma.karma + sign * amount)
        karma.updated_at = datetime.now(UTC)
        counter = _KARMA_COUNTERS.get(reason)
        if counter:
            setattr(karma, counter, max(0, getattr(karma, counter) + sign))
        self._karma[user_id] = karma.model_dump(mode="json")
        return karma

    def _award_karma(self, user_id: str, amount: int, reason: str) -> ContributorKarma:
        return self._adjust_karma(user_id, amount, reason, +1)

    def _revoke_karma(self, user_id: str, amount: int, reason: str) -> None:
        """Take back karma that was granted for content that no longer exists."""
        if user_id in self._karma:
            self._adjust_karma(user_id, amount, reason, -1)

    # ── Story 8.1: User-Submitted Places ─────────────────────────

    async def submit_place(
        self,
        name: str,
        description: str,
        categories: list,
        lat: float,
        lng: float,
        photos: list[str],
        tags: list[str],
        author_id: str,
    ) -> CommunityPlace:
        """Submit a new place for community review."""
        async with self._write_lock:
            self._ensure_loaded()

            # Sanitize text inputs
            name = sanitize_user_input(name)
            description = sanitize_user_input(description)
            tags = [sanitize_user_input(t) for t in tags]
            photos = _clean_photos(photos)

            if len(self._places) >= MAX_PLACES_TOTAL:
                raise CapacityError("Community capacity reached: no more places can be submitted")

            # Per-user daily submission limit. The ledger survives deletion of the place, so
            # submit + delete cycles cannot be used to dodge it (or to farm karma).
            today = self._today()
            user_places_today = max(
                self._quota_used(author_id, "place"),
                sum(
                    1 for d in self._places.values()
                    if d.get("submitted_by") == author_id
                    and d.get("created_at", "")[:10] == today
                ),
            )
            if user_places_today >= MAX_PLACES_PER_USER_PER_DAY:
                raise QuotaError(f"Daily submission limit ({MAX_PLACES_PER_USER_PER_DAY}) reached")

            # Duplicate detection: check for nearby places with the same (normalised) name
            wanted = _norm_name(name)
            for existing in self._places.values():
                coords = existing.get("coordinates", {})
                elat = coords.get("lat", 0)
                elng = coords.get("lng", 0)
                if (abs(elat - lat) < DUPLICATE_DISTANCE_DEG
                        and abs(elng - lng) < DUPLICATE_DISTANCE_DEG):
                    ename = _norm_name(existing.get("name", ""))
                    if ename and ename == wanted:
                        raise ConflictError(
                            f"Duplicate place: a place named '{existing.get('name')}' "
                            "already exists at these coordinates"
                        )

            place_id = f"community_{uuid.uuid4().hex[:12]}"
            place = CommunityPlace(
                id=place_id,
                submitted_by=author_id,
                name=name,
                description=description,
                categories=categories,
                coordinates=Coordinates(lat=lat, lng=lng),
                photos=photos,
                tags=tags,
            )
            self._places[place_id] = place.model_dump(mode="json")
            self._award_karma(author_id, KARMA_PLACE_SUBMITTED, "place_submitted")
            self._quota_bump(author_id, "place")
            self._save_places()
            self._save_karma()
            self._save_quotas()
            logger.info("Place submitted: %s by %s", place_id, author_id)
            return place

    async def confirm_place(self, place_id: str, req: ConfirmPlaceRequest) -> CommunityPlace:
        """Confirm or reject a community-submitted place."""
        async with self._write_lock:
            self._ensure_loaded()
            if place_id not in self._places:
                raise NotFoundError(f"Place {place_id} not found")

            place = CommunityPlace(**self._places[place_id])

            if _same_user(place.submitted_by, req.user_id):
                raise ForbiddenError("Cannot confirm your own submission")
            if place.moderation_status != ModerationStatus.PENDING:
                # The outcome is final: later votes must not flip it or pay the submitter again.
                raise ConflictError(f"Place has already been moderated ({place.moderation_status.value})")

            if req.confirm:
                if req.user_id not in place.confirmations:
                    place.confirmations.append(req.user_id)
                # Remove from rejections if previously rejected
                if req.user_id in place.rejections:
                    place.rejections.remove(req.user_id)

                if len(place.confirmations) >= MODERATION_CONFIRMATIONS_REQUIRED:
                    place.moderation_status = ModerationStatus.CONFIRMED
                    # Award karma to submitter for confirmed place (exactly once: PENDING -> CONFIRMED)
                    self._award_karma(place.submitted_by, KARMA_PLACE_CONFIRMED, "place_confirmed")
                    self._save_karma()
            else:
                if req.user_id not in place.rejections:
                    place.rejections.append(req.user_id)
                if req.user_id in place.confirmations:
                    place.confirmations.remove(req.user_id)

                # If rejections exceed confirmations by threshold, mark rejected
                if len(place.rejections) >= MODERATION_CONFIRMATIONS_REQUIRED:
                    place.moderation_status = ModerationStatus.REJECTED

            place.updated_at = datetime.now(UTC)
            self._places[place_id] = place.model_dump(mode="json")
            self._save_places()
            logger.info("Place %s moderation: status=%s, confirmations=%d, rejections=%d",
                        place_id, place.moderation_status, len(place.confirmations), len(place.rejections))
            return place

    async def get_place(self, place_id: str) -> CommunityPlace | None:
        """Get a single community place by ID."""
        self._ensure_loaded()
        data = self._places.get(place_id)
        return CommunityPlace(**data) if data else None

    async def list_places(
        self,
        status: ModerationStatus | None = None,
        author_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CommunityPlace], int]:
        """List community places with optional filtering."""
        self._ensure_loaded()
        places = [CommunityPlace(**d) for d in self._places.values()]

        if status is not None:
            places = [p for p in places if p.moderation_status == status]
        if author_id is not None:
            places = [p for p in places if p.submitted_by == author_id]

        places.sort(key=lambda p: p.created_at, reverse=True)
        total = len(places)
        offset, limit = max(0, offset), max(0, limit)
        return places[offset: offset + limit], total

    async def suggest_osm(self, place_id: str) -> CommunityPlace:
        """Mark a confirmed place as suggested for OSM integration."""
        async with self._write_lock:
            self._ensure_loaded()
            if place_id not in self._places:
                raise NotFoundError(f"Place {place_id} not found")
            place = CommunityPlace(**self._places[place_id])
            if place.moderation_status != ModerationStatus.CONFIRMED:
                raise ConflictError("Only a confirmed place can be suggested for OpenStreetMap")
            place.osm_suggested = True
            place.updated_at = datetime.now(UTC)
            self._places[place_id] = place.model_dump(mode="json")
            self._save_places()
            return place

    async def get_karma(self, user_id: str) -> ContributorKarma:
        """Get karma for a user (a read never creates a record)."""
        self._ensure_loaded()
        data = self._karma.get(user_id)
        return ContributorKarma(**data) if data else ContributorKarma(user_id=user_id)

    async def update_place(self, place_id: str, author_id: str, **updates) -> CommunityPlace:
        """Update a submitted place (only by the original author, while pending)."""
        async with self._write_lock:
            self._ensure_loaded()
            if place_id not in self._places:
                raise NotFoundError(f"Place {place_id} not found")
            place = CommunityPlace(**self._places[place_id])
            if place.submitted_by != author_id:
                raise ForbiddenError("Only the author can update this place")
            if place.moderation_status != ModerationStatus.PENDING:
                raise ConflictError("Cannot update a place that has been moderated")

            clean: dict = {}
            for key in _PLACE_EDITABLE:  # allow-list: never moderation state or ownership
                val = updates.get(key)
                if val is None:
                    continue
                if key in ("name", "description"):
                    val = sanitize_user_input(val)
                elif key == "tags":
                    val = [sanitize_user_input(t) for t in val]
                elif key == "photos":
                    val = _clean_photos(val)
                clean[key] = val
            updated = CommunityPlace.model_validate({**place.model_dump(), **clean})
            # Votes were cast on the old content: a bait-and-switch edit must not inherit them.
            if any(
                key in clean and getattr(updated, key) != getattr(place, key)
                for key in ("name", "description", "categories")
            ):
                updated.confirmations = []
                updated.rejections = []
            updated.updated_at = datetime.now(UTC)
            self._places[place_id] = updated.model_dump(mode="json")
            self._save_places()
            logger.info("Place updated: %s by %s", place_id, author_id)
            return updated

    async def delete_place(self, place_id: str, author_id: str) -> None:
        """Delete a submitted place (only by the original author)."""
        async with self._write_lock:
            self._ensure_loaded()
            if place_id not in self._places:
                raise NotFoundError(f"Place {place_id} not found")
            place = CommunityPlace(**self._places[place_id])
            if place.submitted_by != author_id:
                raise ForbiddenError("Only the author can delete this place")
            del self._places[place_id]
            # Also delete associated reviews
            to_delete = [rid for rid, r in self._reviews.items() if r.get("place_id") == place_id]
            for rid in to_delete:
                del self._reviews[rid]
            # The karma this place earned goes with it
            self._revoke_karma(author_id, KARMA_PLACE_SUBMITTED, "place_submitted")
            if place.moderation_status == ModerationStatus.CONFIRMED:
                self._revoke_karma(author_id, KARMA_PLACE_CONFIRMED, "place_confirmed")
            self._save_places()
            self._save_karma()
            if to_delete:
                self._save_reviews()
            logger.info("Place deleted: %s by %s", place_id, author_id)

    async def get_place_summary(self, place_id: str) -> PlaceSummary:
        """Get aggregated community activity summary for a place."""
        self._ensure_loaded()
        reviews = [Review(**d) for d in self._reviews.values() if d.get("place_id") == place_id]
        review_count = sum(1 for r in reviews if r.review_type == ReviewType.REVIEW)
        tip_count = sum(1 for r in reviews if r.review_type == ReviewType.TIP)
        avg_score = 0.0
        if reviews:
            scores = [r.score for r in reviews]
            avg_score = round(sum(scores) / len(scores), 1)
        latest_visit = None
        for r in sorted(reviews, key=lambda x: x.created_at, reverse=True):
            if r.visit_date:
                latest_visit = r.visit_date
                break
        return PlaceSummary(
            place_id=place_id,
            review_count=review_count,
            tip_count=tip_count,
            average_score=avg_score,
            latest_visit_date=latest_visit,
        )

    # ── Story 8.2: Reviews & Tips ────────────────────────────────

    async def create_review(
        self,
        place_id: str,
        author_id: str,
        text: str,
        review_type: ReviewType = ReviewType.REVIEW,
        photos: list[str] | None = None,
        visit_date: str | None = None,
    ) -> Review:
        """Create a review or tip for a place."""
        async with self._write_lock:
            self._ensure_loaded()

            # Sanitize text inputs
            text = sanitize_user_input(text)
            visit_date = _clean_visit_date(visit_date)
            photos = _clean_photos(photos or [])

            if len(self._reviews) >= MAX_REVIEWS_TOTAL:
                raise CapacityError("Community capacity reached: no more reviews can be added")
            if self._quota_used(author_id, "review") >= MAX_REVIEWS_PER_USER_PER_DAY:
                raise QuotaError(f"Daily review limit ({MAX_REVIEWS_PER_USER_PER_DAY}) reached")

            same_kind = [
                r for r in self._reviews.values()
                if r.get("place_id") == place_id
                and r.get("author_id") == author_id
                and r.get("review_type") == review_type.value
            ]
            # Limit one review per user per place (tips are allowed, but pay no extra karma)
            if review_type == ReviewType.REVIEW and len(same_kind) >= MAX_REVIEWS_PER_USER_PER_PLACE:
                raise ConflictError("You already have a review for this place. Update it instead.")

            review_id = f"review_{uuid.uuid4().hex[:12]}"
            pays_karma = not same_kind
            review = Review(
                id=review_id,
                place_id=place_id,
                author_id=author_id,
                review_type=review_type,
                text=text,
                photos=photos,
                visit_date=visit_date,
                karma_awarded=pays_karma,
            )
            self._reviews[review_id] = review.model_dump(mode="json")
            if pays_karma:
                self._award_karma(author_id, KARMA_REVIEW_WRITTEN, "review_written")
            self._quota_bump(author_id, "review")
            self._save_reviews()
            self._save_karma()
            self._save_quotas()
            logger.info("Review created: %s for place %s by %s", review_id, place_id, author_id)
            return review

    async def vote_review(self, review_id: str, user_id: str, upvote: bool) -> Review:
        """Upvote or downvote a review (one vote per user; repeating it changes nothing)."""
        async with self._write_lock:
            self._ensure_loaded()
            if review_id not in self._reviews:
                raise NotFoundError(f"Review {review_id} not found")

            review = Review(**self._reviews[review_id])

            if _same_user(review.author_id, user_id):
                raise ForbiddenError("Cannot vote on your own review")

            already_up = user_id in review.upvotes
            already_down = user_id in review.downvotes
            if (upvote and already_up) or (not upvote and already_down):
                return review  # same vote again: idempotent, no karma

            # Remove previous (opposite) vote; taking back an upvote takes back its karma
            if already_up:
                review.upvotes.remove(user_id)
                self._revoke_karma(review.author_id, KARMA_HELPFUL_VOTE, "helpful_vote")
            if already_down:
                review.downvotes.remove(user_id)

            if upvote:
                review.upvotes.append(user_id)
                self._award_karma(review.author_id, KARMA_HELPFUL_VOTE, "helpful_vote")
            else:
                review.downvotes.append(user_id)
            self._save_karma()

            review.updated_at = datetime.now(UTC)
            self._reviews[review_id] = review.model_dump(mode="json")
            self._save_reviews()
            return review

    async def list_reviews(
        self,
        place_id: str,
        review_type: ReviewType | None = None,
        sort_by: str = "score",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Review], int]:
        """List reviews for a place, sorted by helpfulness score."""
        self._ensure_loaded()
        reviews = [
            Review(**d) for d in self._reviews.values()
            if d.get("place_id") == place_id
        ]

        if review_type is not None:
            reviews = [r for r in reviews if r.review_type == review_type]

        if sort_by == "score":
            reviews.sort(key=lambda r: r.score, reverse=True)
        elif sort_by == "recent":
            reviews.sort(key=lambda r: r.created_at, reverse=True)

        total = len(reviews)
        offset, limit = max(0, offset), max(0, limit)
        return reviews[offset: offset + limit], total

    async def get_review(self, review_id: str) -> Review | None:
        """Get a single review by ID."""
        self._ensure_loaded()
        data = self._reviews.get(review_id)
        return Review(**data) if data else None

    async def update_review(self, review_id: str, author_id: str, **updates) -> Review:
        """Update a review (only by the original author)."""
        async with self._write_lock:
            self._ensure_loaded()
            if review_id not in self._reviews:
                raise NotFoundError(f"Review {review_id} not found")
            review = Review(**self._reviews[review_id])
            if review.author_id != author_id:
                raise ForbiddenError("Only the author can update this review")
            for key in _REVIEW_EDITABLE:  # allow-list: never votes, author or karma flags
                val = updates.get(key)
                if val is None:
                    continue
                if key == "text":
                    val = sanitize_user_input(val)
                elif key == "photos":
                    val = _clean_photos(val)
                elif key == "visit_date":
                    val = _clean_visit_date(val)
                setattr(review, key, val)
            review.updated_at = datetime.now(UTC)
            self._reviews[review_id] = review.model_dump(mode="json")
            self._save_reviews()
            logger.info("Review updated: %s by %s", review_id, author_id)
            return review

    async def delete_review(self, review_id: str, author_id: str) -> None:
        """Delete a review (only by the original author)."""
        async with self._write_lock:
            self._ensure_loaded()
            if review_id not in self._reviews:
                raise NotFoundError(f"Review {review_id} not found")
            review = Review(**self._reviews[review_id])
            if review.author_id != author_id:
                raise ForbiddenError("Only the author can delete this review")
            del self._reviews[review_id]
            if review.karma_awarded:
                self._revoke_karma(author_id, KARMA_REVIEW_WRITTEN, "review_written")
                self._save_karma()
            self._save_reviews()
            logger.info("Review deleted: %s by %s", review_id, author_id)

    async def report_outdated(self, review_id: str, user_id: str, reason: str = "") -> Review:
        """Flag a review's information as outdated."""
        async with self._write_lock:
            self._ensure_loaded()
            if review_id not in self._reviews:
                raise NotFoundError(f"Review {review_id} not found")
            review = Review(**self._reviews[review_id])
            # A report is a downvote; it replaces (and takes the karma back from) an upvote
            if user_id in review.upvotes:
                review.upvotes.remove(user_id)
                self._revoke_karma(review.author_id, KARMA_HELPFUL_VOTE, "helpful_vote")
                self._save_karma()
            if user_id not in review.downvotes:
                review.downvotes.append(user_id)
            review.updated_at = datetime.now(UTC)
            self._reviews[review_id] = review.model_dump(mode="json")
            self._save_reviews()
            logger.info("Review %s reported as outdated by %s: %r", review_id, user_id, reason[:200])
            return review

    # ── Story 8.3: Social Routes ─────────────────────────────────

    async def publish_route(
        self,
        author_id: str,
        title: str,
        description: str,
        region: str,
        waypoint_place_ids: list[str],
        distance_km: float,
        duration_hours: float,
        tags: list[str],
    ) -> PublishedRoute:
        """Publish a route for the community."""
        async with self._write_lock:
            self._ensure_loaded()
            if len(self._routes) >= MAX_ROUTES_TOTAL:
                raise CapacityError("Community capacity reached: no more routes can be published")
            if self._quota_used(author_id, "route") >= MAX_ROUTES_PER_USER_PER_DAY:
                raise QuotaError(f"Daily route limit ({MAX_ROUTES_PER_USER_PER_DAY}) reached")
            route_id = f"route_{uuid.uuid4().hex[:12]}"
            route = PublishedRoute(
                id=route_id,
                author_id=author_id,
                title=sanitize_user_input(title),
                description=sanitize_user_input(description),
                region=sanitize_user_input(region),
                waypoint_place_ids=waypoint_place_ids,
                distance_km=distance_km,
                duration_hours=duration_hours,
                tags=[sanitize_user_input(t) for t in tags],
            )
            self._routes[route_id] = route.model_dump(mode="json")
            self._award_karma(author_id, KARMA_ROUTE_PUBLISHED, "route_published")
            self._quota_bump(author_id, "route")
            self._save_routes()
            self._save_karma()
            self._save_quotas()
            logger.info("Route published: %s by %s", route_id, author_id)
            return route

    async def rate_route(self, route_id: str, user_id: str, score: int, comment: str = "") -> PublishedRoute:
        """Rate a published route."""
        async with self._write_lock:
            self._ensure_loaded()
            if route_id not in self._routes:
                raise NotFoundError(f"Route {route_id} not found")

            route = PublishedRoute(**self._routes[route_id])

            if _same_user(route.author_id, user_id):
                raise ForbiddenError("Cannot rate your own route")

            # Replace existing rating from this user
            route.ratings = [r for r in route.ratings if r.user_id != user_id]
            route.ratings.append(
                RouteRating(user_id=user_id, score=score, comment=sanitize_user_input(comment))
            )

            route.updated_at = datetime.now(UTC)
            self._routes[route_id] = route.model_dump(mode="json")
            self._save_routes()
            return route

    async def list_routes(
        self,
        region: str | None = None,
        author_id: str | None = None,
        sort_by: str = "rating",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PublishedRoute], int]:
        """List published routes, optionally filtered by region."""
        self._ensure_loaded()
        routes = [PublishedRoute(**d) for d in self._routes.values()]

        if region:
            region_lower = region.lower()
            routes = [r for r in routes if region_lower in r.region.lower()]
        if author_id:
            routes = [r for r in routes if r.author_id == author_id]

        if sort_by == "rating":
            routes.sort(key=lambda r: r.average_rating, reverse=True)
        elif sort_by == "recent":
            routes.sort(key=lambda r: r.created_at, reverse=True)

        total = len(routes)
        offset, limit = max(0, offset), max(0, limit)
        return routes[offset: offset + limit], total

    async def get_route(self, route_id: str) -> PublishedRoute | None:
        """Get a single route by ID."""
        self._ensure_loaded()
        data = self._routes.get(route_id)
        return PublishedRoute(**data) if data else None

    # ── Follow System ────────────────────────────────────────────

    async def follow(self, follower_id: str, following_id: str) -> ExplorerFollow:
        """Request to follow an explorer (requires consent)."""
        async with self._write_lock:
            self._ensure_loaded()
            if _same_user(follower_id, following_id):
                raise ValueError("Cannot follow yourself")

            follow_key = f"{follower_id}:{following_id}"
            existing = self._follows.get(follow_key)
            if existing:
                # Idempotent: re-requesting must not wipe a consent that was already granted.
                return ExplorerFollow(**existing)
            if len(self._follows) >= MAX_FOLLOWS_TOTAL:
                raise CapacityError("Community capacity reached: no more follows can be added")
            follow = ExplorerFollow(
                follower_id=follower_id,
                following_id=following_id,
                consent_given=False,
            )
            self._follows[follow_key] = follow.model_dump(mode="json")
            self._save_follows()
            return follow

    async def grant_consent(self, req: ConsentRequest) -> ExplorerFollow:
        """Grant or revoke consent for a follower to see your discoveries."""
        async with self._write_lock:
            self._ensure_loaded()
            follow_key = f"{req.follower_id}:{req.user_id}"
            if follow_key not in self._follows:
                raise NotFoundError("Follow relationship not found")

            follow = ExplorerFollow(**self._follows[follow_key])
            follow.consent_given = req.consent
            self._follows[follow_key] = follow.model_dump(mode="json")
            self._save_follows()
            return follow

    async def unfollow(self, follower_id: str, following_id: str) -> None:
        """Remove a follow relationship."""
        async with self._write_lock:
            self._ensure_loaded()
            follow_key = f"{follower_id}:{following_id}"
            if follow_key in self._follows:
                del self._follows[follow_key]
                self._save_follows()

    async def get_followers(self, user_id: str) -> list[ExplorerFollow]:
        """Get list of users following this user."""
        self._ensure_loaded()
        return [
            ExplorerFollow(**d) for d in self._follows.values()
            if d.get("following_id") == user_id
        ]

    async def get_following(self, user_id: str) -> list[ExplorerFollow]:
        """Get list of users this user is following."""
        self._ensure_loaded()
        return [
            ExplorerFollow(**d) for d in self._follows.values()
            if d.get("follower_id") == user_id
        ]


# Module-level singleton
_community_service: CommunityService | None = None


def get_community_service() -> CommunityService:
    """Get or create the community service singleton."""
    global _community_service
    if _community_service is None:
        _community_service = CommunityService()
    return _community_service
