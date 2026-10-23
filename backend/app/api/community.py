"""Community API endpoints (Epic 8) — User-generated content and social features."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models.community import (
    CommunityPlaceListResponse,
    ConfirmPlaceRequest,
    ConsentRequest,
    CreateReviewRequest,
    FollowListResponse,
    FollowRequest,
    KarmaResponse,
    ModerationStatus,
    PlaceSummary,
    PublishedRouteListResponse,
    PublishRouteRequest,
    RateRouteRequest,
    ReportOutdatedRequest,
    ReviewListResponse,
    ReviewType,
    SubmitPlaceRequest,
    UpdatePlaceRequest,
    UpdateReviewRequest,
    VoteRequest,
)
from app.services.community import (
    CapacityError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    QuotaError,
    get_community_service,
)
from app.utils.safe_route import SafeValidationRoute

router = APIRouter(prefix="/api/community", tags=["community"], route_class=SafeValidationRoute)

# Shared paging bounds: negative offsets / limits silently slice from the end of the list
Limit = Query(50, ge=1, le=200)
Offset = Query(0, ge=0)


def _http_error(exc: ValueError) -> HTTPException:
    """Map the service's refusal to the matching status code (never a bare 500)."""
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ForbiddenError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, QuotaError):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, CapacityError):
        return HTTPException(status_code=507, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# ── Story 8.1: User-Submitted Places ────────────────────────────


@router.post("/places")
async def submit_place(req: SubmitPlaceRequest):
    """Submit a new place for community review."""
    svc = get_community_service()
    try:
        return await svc.submit_place(
            name=req.name,
            description=req.description,
            categories=req.categories,
            lat=req.lat,
            lng=req.lng,
            photos=req.photos,
            tags=req.tags,
            author_id=req.author_id,
        )
    except ValueError as e:
        raise _http_error(e) from e


@router.post("/places/{place_id}/confirm")
async def confirm_place(place_id: str, req: ConfirmPlaceRequest):
    """Confirm or reject a community-submitted place."""
    svc = get_community_service()
    try:
        return await svc.confirm_place(place_id, req)
    except ValueError as e:
        raise _http_error(e) from e


@router.get("/places", response_model=CommunityPlaceListResponse)
async def list_places(
    status: ModerationStatus | None = None,
    author_id: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
):
    """List community-submitted places."""
    svc = get_community_service()
    places, total = await svc.list_places(status=status, author_id=author_id, limit=limit, offset=offset)
    return CommunityPlaceListResponse(places=places, total=total)


@router.get("/places/{place_id}")
async def get_place(place_id: str):
    """Get a community place by ID."""
    svc = get_community_service()
    place = await svc.get_place(place_id)
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")
    return place


@router.post("/places/{place_id}/osm-suggest")
async def suggest_osm(place_id: str):
    """Mark a confirmed place as suggested for OpenStreetMap."""
    svc = get_community_service()
    try:
        return await svc.suggest_osm(place_id)
    except ValueError as e:
        raise _http_error(e) from e


@router.patch("/places/{place_id}")
async def update_place(place_id: str, req: UpdatePlaceRequest, author_id: str = ""):
    """Update a submitted place (only by the author, while pending)."""
    if not author_id:
        raise HTTPException(status_code=400, detail="author_id query parameter required")
    svc = get_community_service()
    try:
        return await svc.update_place(place_id, author_id, **req.model_dump(exclude_none=True))
    except ValueError as e:
        raise _http_error(e) from e


@router.delete("/places/{place_id}")
async def delete_place(place_id: str, author_id: str = ""):
    """Delete a submitted place (only by the author)."""
    if not author_id:
        raise HTTPException(status_code=400, detail="author_id query parameter required")
    svc = get_community_service()
    try:
        await svc.delete_place(place_id, author_id)
        return {"status": "deleted"}
    except ValueError as e:
        raise _http_error(e) from e


@router.get("/places/{place_id}/summary", response_model=PlaceSummary)
async def get_place_summary(place_id: str):
    """Get community activity summary for a place."""
    svc = get_community_service()
    return await svc.get_place_summary(place_id)


@router.get("/karma/{user_id}", response_model=KarmaResponse)
async def get_karma(user_id: str):
    """Get karma for a contributor."""
    svc = get_community_service()
    karma = await svc.get_karma(user_id)
    return KarmaResponse(karma=karma)


# ── Story 8.2: Reviews & Tips ────────────────────────────────────


@router.post("/places/{place_id}/reviews")
async def create_review(place_id: str, req: CreateReviewRequest):
    """Write a review or tip for a place."""
    svc = get_community_service()
    try:
        return await svc.create_review(
            place_id=place_id,
            author_id=req.author_id,
            text=req.text,
            review_type=req.review_type,
            photos=req.photos,
            visit_date=req.visit_date,
        )
    except ValueError as e:
        raise _http_error(e) from e


@router.get("/places/{place_id}/reviews", response_model=ReviewListResponse)
async def list_reviews(
    place_id: str,
    review_type: ReviewType | None = None,
    sort_by: str = Query("score", pattern="^(score|recent)$"),
    limit: int = Limit,
    offset: int = Offset,
):
    """List reviews for a place, sorted by helpfulness."""
    svc = get_community_service()
    reviews, total = await svc.list_reviews(
        place_id=place_id, review_type=review_type, sort_by=sort_by, limit=limit, offset=offset
    )
    return ReviewListResponse(reviews=reviews, total=total)


@router.post("/reviews/{review_id}/vote")
async def vote_review(review_id: str, req: VoteRequest):
    """Upvote or downvote a review."""
    svc = get_community_service()
    try:
        return await svc.vote_review(review_id, req.user_id, req.upvote)
    except ValueError as e:
        raise _http_error(e) from e


@router.patch("/reviews/{review_id}")
async def update_review(review_id: str, req: UpdateReviewRequest, author_id: str = ""):
    """Update a review (only by the author)."""
    if not author_id:
        raise HTTPException(status_code=400, detail="author_id query parameter required")
    svc = get_community_service()
    try:
        return await svc.update_review(review_id, author_id, **req.model_dump(exclude_none=True))
    except ValueError as e:
        raise _http_error(e) from e


@router.delete("/reviews/{review_id}")
async def delete_review(review_id: str, author_id: str = ""):
    """Delete a review (only by the author)."""
    if not author_id:
        raise HTTPException(status_code=400, detail="author_id query parameter required")
    svc = get_community_service()
    try:
        await svc.delete_review(review_id, author_id)
        return {"status": "deleted"}
    except ValueError as e:
        raise _http_error(e) from e


@router.post("/reviews/{review_id}/report-outdated")
async def report_outdated(review_id: str, req: ReportOutdatedRequest):
    """Flag a review's information as outdated."""
    svc = get_community_service()
    try:
        return await svc.report_outdated(review_id, req.user_id, req.reason)
    except ValueError as e:
        raise _http_error(e) from e


# ── Story 8.3: Social Routes ────────────────────────────────────


@router.post("/routes")
async def publish_route(req: PublishRouteRequest):
    """Publish a route for the community."""
    svc = get_community_service()
    try:
        return await svc.publish_route(
            author_id=req.author_id,
            title=req.title,
            description=req.description,
            region=req.region,
            waypoint_place_ids=req.waypoint_place_ids,
            distance_km=req.distance_km,
            duration_hours=req.duration_hours,
            tags=req.tags,
        )
    except ValueError as e:
        raise _http_error(e) from e


@router.get("/routes", response_model=PublishedRouteListResponse)
async def list_routes(
    region: str | None = None,
    author_id: str | None = None,
    sort_by: str = Query("rating", pattern="^(rating|recent)$"),
    limit: int = Limit,
    offset: int = Offset,
):
    """List published routes, optionally filtered by region."""
    svc = get_community_service()
    routes, total = await svc.list_routes(
        region=region, author_id=author_id, sort_by=sort_by, limit=limit, offset=offset
    )
    return PublishedRouteListResponse(routes=routes, total=total)


@router.get("/routes/{route_id}")
async def get_route(route_id: str):
    """Get a published route by ID."""
    svc = get_community_service()
    route = await svc.get_route(route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    return route


@router.post("/routes/{route_id}/rate")
async def rate_route(route_id: str, req: RateRouteRequest):
    """Rate a published route."""
    svc = get_community_service()
    try:
        return await svc.rate_route(route_id, req.user_id, req.score, req.comment)
    except ValueError as e:
        raise _http_error(e) from e


# ── Follow System ────────────────────────────────────────────────


@router.post("/follow")
async def follow_explorer(req: FollowRequest):
    """Request to follow an explorer."""
    svc = get_community_service()
    try:
        return await svc.follow(req.follower_id, req.following_id)
    except ValueError as e:
        raise _http_error(e) from e


@router.post("/follow/consent")
async def grant_consent(req: ConsentRequest):
    """Grant or revoke consent for a follower to see your discoveries."""
    svc = get_community_service()
    try:
        return await svc.grant_consent(req)
    except ValueError as e:
        raise _http_error(e) from e


@router.delete("/follow/{follower_id}/{following_id}")
async def unfollow(follower_id: str, following_id: str):
    """Unfollow an explorer."""
    svc = get_community_service()
    await svc.unfollow(follower_id, following_id)
    return {"status": "unfollowed"}


@router.get("/followers/{user_id}", response_model=FollowListResponse)
async def get_followers(user_id: str):
    """Get followers of an explorer."""
    svc = get_community_service()
    follows = await svc.get_followers(user_id)
    return FollowListResponse(follows=follows, total=len(follows))


@router.get("/following/{user_id}", response_model=FollowListResponse)
async def get_following(user_id: str):
    """Get explorers this user is following."""
    svc = get_community_service()
    follows = await svc.get_following(user_id)
    return FollowListResponse(follows=follows, total=len(follows))
