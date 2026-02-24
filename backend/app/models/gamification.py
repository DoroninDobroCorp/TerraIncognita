"""Gamification domain models (Epic 6).

Story 6.1: Fog of War — tracking explored territory
Story 6.2: Achievement System — badges & achievements
Story 6.3: Explorer Level — XP & progression
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Story 6.1: Fog of War Mechanics ─────────────────────────────


class FogCell(BaseModel):
    """A single explored cell on the grid (approx 100m × 100m)."""

    lat: float
    lng: float
    explored_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    visit_id: str | None = None


class FogRegionStats(BaseModel):
    """Fog of War coverage stats for a specific region."""

    region_name: str
    total_cells: int = 0
    explored_cells: int = 0
    coverage_percent: float = 0.0


class FogOfWarState(BaseModel):
    """Current Fog of War state for the user."""

    total_explored_cells: int = 0
    total_explored_area_km2: float = 0.0
    regions: list[FogRegionStats] = Field(default_factory=list)
    gps_trail: list[FogCell] = Field(default_factory=list)


class GpsTrailPoint(BaseModel):
    """A single GPS trail point for recording movement."""

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FogRevealRequest(BaseModel):
    """POST /api/fog/reveal — reveal fog at a location (manual or GPS trail)."""

    points: list[GpsTrailPoint] = Field(..., min_length=1, max_length=1000)
    radius_m: float = Field(50.0, gt=0, le=500)


class FogRevealResponse(BaseModel):
    """Response for fog reveal."""

    new_cells_revealed: int = 0
    total_explored_cells: int = 0
    total_explored_area_km2: float = 0.0


class FogStatusResponse(BaseModel):
    """GET /api/fog/status response."""

    state: FogOfWarState


class FogRegionRequest(BaseModel):
    """GET /api/fog/region — get coverage for a specific area."""

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(5.0, gt=0, le=50)
    region_name: str = "custom"


# ── Story 6.2: Achievement System ───────────────────────────────


class AchievementCategory(str, enum.Enum):
    """Types of achievements."""

    EXPLORATION = "exploration"
    BEHAVIOR = "behavior"
    SURPRISE = "surprise"
    REGIONAL = "regional"


class AchievementTier(str, enum.Enum):
    """Achievement rarity/tier."""

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"


class AchievementDefinition(BaseModel):
    """Static definition of an achievement."""

    id: str
    name: str
    description: str
    icon: str = "🏆"
    category: AchievementCategory
    tier: AchievementTier = AchievementTier.BRONZE
    condition_type: str
    condition_value: int | float = 1
    xp_reward: int = 10
    hidden: bool = False


class UserAchievement(BaseModel):
    """An achievement earned by the user."""

    achievement_id: str
    unlocked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    progress: float = 0.0
    completed: bool = False
    notified: bool = False


class AchievementProgress(BaseModel):
    """Progress towards an achievement."""

    definition: AchievementDefinition
    current_value: float = 0.0
    target_value: float = 1.0
    progress_percent: float = 0.0
    completed: bool = False
    unlocked_at: datetime | None = None


class AchievementListResponse(BaseModel):
    """GET /api/achievements response."""

    achievements: list[AchievementProgress]
    total_unlocked: int = 0
    total_available: int = 0


class NewAchievementsResponse(BaseModel):
    """Newly unlocked achievements (returned after actions)."""

    new_achievements: list[AchievementProgress] = Field(default_factory=list)
    xp_earned: int = 0


# ── Story 6.3: Explorer Level ───────────────────────────────────


class ExplorerLevel(str, enum.Enum):
    """Explorer progression levels."""

    NOVICE = "novice"
    SCOUT = "scout"
    EXPLORER = "explorer"
    PATHFINDER = "pathfinder"
    TRAILBLAZER = "trailblazer"
    LEGEND = "legend"


class LevelInfo(BaseModel):
    """Information about a specific level."""

    level: ExplorerLevel
    min_xp: int
    max_xp: int | None = None
    unlocked_features: list[str] = Field(default_factory=list)


class ExplorerProfile(BaseModel):
    """User's gamification profile."""

    total_xp: int = 0
    level: ExplorerLevel = ExplorerLevel.NOVICE
    level_info: LevelInfo | None = None
    xp_to_next_level: int = 0
    level_progress_percent: float = 0.0
    achievements_unlocked: int = 0
    total_achievements: int = 0
    fog_explored_km2: float = 0.0
    title: str = "Novice Explorer"


class XpEvent(BaseModel):
    """An XP-earning event."""

    id: str
    source: str
    xp: int
    description: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    visit_id: str | None = None


class XpEventResponse(BaseModel):
    """Response showing XP earned."""

    xp_earned: int = 0
    total_xp: int = 0
    level: ExplorerLevel = ExplorerLevel.NOVICE
    leveled_up: bool = False
    new_level: ExplorerLevel | None = None


class ExplorerProfileResponse(BaseModel):
    """GET /api/explorer/profile response."""

    profile: ExplorerProfile


class XpHistoryResponse(BaseModel):
    """GET /api/explorer/xp-history response."""

    events: list[XpEvent]
    total: int


class LeaderboardEntry(BaseModel):
    """A single entry in the leaderboard."""

    user_id: str = "self"
    username: str = "You"
    total_xp: int = 0
    level: ExplorerLevel = ExplorerLevel.NOVICE
    explored_km2: float = 0.0
    achievements_count: int = 0


class LeaderboardResponse(BaseModel):
    """GET /api/explorer/leaderboard response."""

    entries: list[LeaderboardEntry]
    your_rank: int = 1
