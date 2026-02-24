"""Gamification API endpoints (Epic 6)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.gamification import (
    AchievementListResponse,
    ExplorerProfileResponse,
    FogRegionRequest,
    FogRegionStats,
    FogRevealRequest,
    FogRevealResponse,
    FogStatusResponse,
    LeaderboardResponse,
    XpHistoryResponse,
)
from app.services import gamification as gam_service

router = APIRouter(prefix="/api", tags=["gamification"])


# ── Story 6.1: Fog of War ───────────────────────────────────────


@router.post("/fog/reveal", response_model=FogRevealResponse)
async def reveal_fog(req: FogRevealRequest) -> FogRevealResponse:
    """Reveal fog of war from GPS trail points."""
    points = [(p.lat, p.lng, p.timestamp) for p in req.points]
    return await gam_service.reveal_fog(points, radius_m=req.radius_m)


@router.get("/fog/status", response_model=FogStatusResponse)
async def get_fog_status() -> FogStatusResponse:
    """Get current fog of war state."""
    state = await gam_service.get_fog_status()
    return FogStatusResponse(state=state)


@router.post("/fog/region", response_model=FogRegionStats)
async def get_fog_region(req: FogRegionRequest) -> FogRegionStats:
    """Get fog coverage for a specific region."""
    return await gam_service.get_fog_region(
        lat=req.lat, lng=req.lng, radius_km=req.radius_km, region_name=req.region_name
    )


@router.get("/fog/cells")
async def get_fog_cells(
    lat: float, lng: float, radius_km: float = 5.0
) -> dict:
    """Get explored cells within an area (for map rendering)."""
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        raise HTTPException(status_code=422, detail="Invalid coordinates")
    if not (0 < radius_km <= 50):
        raise HTTPException(status_code=422, detail="radius_km must be between 0 and 50")
    cells = await gam_service.get_explored_cells_in_area(lat, lng, radius_km)
    return {
        "cells": [{"lat": c.lat, "lng": c.lng, "explored_at": c.explored_at} for c in cells],
        "total": len(cells),
    }


# ── Story 6.2: Achievements ─────────────────────────────────────


@router.get("/achievements", response_model=AchievementListResponse)
async def get_achievements() -> AchievementListResponse:
    """Get all achievements with progress."""
    achievements, unlocked, total = await gam_service.get_achievements()
    return AchievementListResponse(
        achievements=achievements,
        total_unlocked=unlocked,
        total_available=total,
    )


# ── Story 6.3: Explorer Level ───────────────────────────────────


@router.get("/explorer/profile", response_model=ExplorerProfileResponse)
async def get_profile() -> ExplorerProfileResponse:
    """Get explorer gamification profile."""
    profile = await gam_service.get_explorer_profile()
    return ExplorerProfileResponse(profile=profile)


@router.get("/explorer/xp-history", response_model=XpHistoryResponse)
async def get_xp_history(limit: int = 50, offset: int = 0) -> XpHistoryResponse:
    """Get XP earning history."""
    events, total = await gam_service.get_xp_history(limit=limit, offset=offset)
    return XpHistoryResponse(events=events, total=total)


@router.get("/explorer/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard() -> LeaderboardResponse:
    """Get explorer leaderboard."""
    entries = await gam_service.get_leaderboard()
    return LeaderboardResponse(entries=entries, your_rank=1)
