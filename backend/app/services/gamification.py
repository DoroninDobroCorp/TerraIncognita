"""Gamification service — business logic for Epic 6.

Module organisation:
  - Fog of War: GPS trail → grid cells → coverage stats
  - Achievements: definitions, checking, unlocking
  - Explorer Level: XP earning, level progression, feature unlocks
  - Persistence: JSON-file backed (same pattern as journal)

Thread safety: atomic writes via temp-file-then-rename.
Concurrency: _write_lock serialises concurrent mutations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import settings
from app.models.gamification import (
    AchievementCategory,
    AchievementDefinition,
    AchievementProgress,
    AchievementTier,
    ExplorerLevel,
    ExplorerProfile,
    FogCell,
    FogOfWarState,
    FogRegionStats,
    FogRevealResponse,
    LeaderboardEntry,
    LevelInfo,
    NewAchievementsResponse,
    UserAchievement,
    XpEvent,
    XpEventResponse,
)
from app.models.journal import PlaceNote, Visit, VisitStatus

logger = logging.getLogger(__name__)

# ── In-memory store (JSON-file backed) ───────────────────────────

_fog_cells: dict[str, FogCell] = {}  # key: "lat_lng" grid key
_achievements: dict[str, UserAchievement] = {}  # key: achievement_id
_xp_events: list[XpEvent] = []
_total_xp: int = 0
_data_dir: Path | None = None
_write_lock = asyncio.Lock()


def _get_data_dir() -> Path:
    global _data_dir
    if _data_dir is None:
        _data_dir = Path(settings.gamification_data_dir)
        _data_dir.mkdir(parents=True, exist_ok=True)
    return _data_dir


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def _save_fog() -> None:
    path = _get_data_dir() / "fog_cells.json"
    data = {k: v.model_dump(mode="json") for k, v in _fog_cells.items()}
    _atomic_write(path, json.dumps(data, default=str, ensure_ascii=False))


def _save_achievements() -> None:
    path = _get_data_dir() / "achievements.json"
    data = {k: v.model_dump(mode="json") for k, v in _achievements.items()}
    _atomic_write(path, json.dumps(data, default=str, ensure_ascii=False))


def _save_xp() -> None:
    path = _get_data_dir() / "xp_events.json"
    data = {
        "total_xp": _total_xp,
        "events": [e.model_dump(mode="json") for e in _xp_events],
    }
    _atomic_write(path, json.dumps(data, default=str, ensure_ascii=False))


def _load_store() -> None:
    global _total_xp
    data_dir = _get_data_dir()

    fog_path = data_dir / "fog_cells.json"
    if fog_path.exists() and not _fog_cells:
        try:
            raw = json.loads(fog_path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                _fog_cells[k] = FogCell.model_validate(v)
        except Exception:
            logger.warning("Failed to load fog_cells.json, starting fresh")

    ach_path = data_dir / "achievements.json"
    if ach_path.exists() and not _achievements:
        try:
            raw = json.loads(ach_path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                _achievements[k] = UserAchievement.model_validate(v)
        except Exception:
            logger.warning("Failed to load achievements.json, starting fresh")

    xp_path = data_dir / "xp_events.json"
    if xp_path.exists() and not _xp_events:
        try:
            raw = json.loads(xp_path.read_text(encoding="utf-8"))
            _total_xp = raw.get("total_xp", 0)
            for e in raw.get("events", []):
                _xp_events.append(XpEvent.model_validate(e))
        except Exception:
            logger.warning("Failed to load xp_events.json, starting fresh")


def _ensure_loaded() -> None:
    _load_store()


# ── Grid utilities ───────────────────────────────────────────────

# Cell size: ~100m at equator (0.001 degrees ≈ 111m)
_CELL_SIZE = 0.001
_CELL_AREA_KM2 = 0.0111 * 0.0111  # approx area of one cell in km²


def _cell_key(lat: float, lng: float) -> str:
    """Quantize coordinates to grid cell key."""
    grid_lat = round(lat / _CELL_SIZE) * _CELL_SIZE
    grid_lng = round(lng / _CELL_SIZE) * _CELL_SIZE
    return f"{grid_lat:.4f}_{grid_lng:.4f}"


def _cell_coords(key: str) -> tuple[float, float]:
    parts = key.split("_")
    return float(parts[0]), float(parts[1])


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _cells_in_radius(lat: float, lng: float, radius_m: float) -> list[str]:
    """Get all grid cell keys within radius of a point."""
    # Convert radius to approximate grid steps
    steps = max(1, int(radius_m / (111.0 * _CELL_SIZE * 1000)) + 1)
    cells = []
    for dlat in range(-steps, steps + 1):
        for dlng in range(-steps, steps + 1):
            clat = lat + dlat * _CELL_SIZE
            clng = lng + dlng * _CELL_SIZE
            dist = _haversine_m(lat, lng, clat, clng)
            if dist <= radius_m:
                cells.append(_cell_key(clat, clng))
    return cells


# ── Story 6.1: Fog of War ───────────────────────────────────────


async def reveal_fog(
    points: list[tuple[float, float, datetime | None]],
    radius_m: float = 50.0,
    visit_id: str | None = None,
) -> FogRevealResponse:
    """Reveal fog of war cells from GPS trail points."""
    _ensure_loaded()
    new_count = 0

    async with _write_lock:
        for lat, lng, ts in points:
            cell_keys = _cells_in_radius(lat, lng, radius_m)
            for key in cell_keys:
                if key not in _fog_cells:
                    clat, clng = _cell_coords(key)
                    _fog_cells[key] = FogCell(
                        lat=clat,
                        lng=clng,
                        explored_at=ts or datetime.now(UTC),
                        visit_id=visit_id,
                    )
                    new_count += 1

        _save_fog()

    total = len(_fog_cells)
    area = total * _CELL_AREA_KM2

    logger.info("Fog revealed: %d new cells, %d total (%.3f km²)", new_count, total, area)
    return FogRevealResponse(
        new_cells_revealed=new_count,
        total_explored_cells=total,
        total_explored_area_km2=round(area, 4),
    )


async def get_fog_status() -> FogOfWarState:
    """Get current fog of war state."""
    _ensure_loaded()
    total = len(_fog_cells)
    area = total * _CELL_AREA_KM2

    return FogOfWarState(
        total_explored_cells=total,
        total_explored_area_km2=round(area, 4),
        gps_trail=list(_fog_cells.values())[-100:],  # last 100 cells
    )


async def get_fog_region(
    lat: float, lng: float, radius_km: float, region_name: str = "custom"
) -> FogRegionStats:
    """Get fog coverage for a specific region."""
    _ensure_loaded()
    radius_m = radius_km * 1000

    # Estimate total cells in the region
    total_cells = len(_cells_in_radius(lat, lng, radius_m))

    # Count explored cells in region
    explored = 0
    for key, cell in _fog_cells.items():
        dist = _haversine_m(lat, lng, cell.lat, cell.lng)
        if dist <= radius_m:
            explored += 1

    coverage = (explored / total_cells * 100) if total_cells > 0 else 0.0
    return FogRegionStats(
        region_name=region_name,
        total_cells=total_cells,
        explored_cells=explored,
        coverage_percent=round(coverage, 2),
    )


async def get_explored_cells_in_area(
    lat: float, lng: float, radius_km: float
) -> list[FogCell]:
    """Get explored cells within an area (for map rendering)."""
    _ensure_loaded()
    radius_m = radius_km * 1000
    result = []
    for cell in _fog_cells.values():
        dist = _haversine_m(lat, lng, cell.lat, cell.lng)
        if dist <= radius_m:
            result.append(cell)
    return result


# ── Story 6.2: Achievement System ───────────────────────────────

# Achievement definitions
ACHIEVEMENTS: list[AchievementDefinition] = [
    # Exploration category
    AchievementDefinition(
        id="first_visit",
        name="First Steps",
        description="Visit your first place",
        icon="👣",
        category=AchievementCategory.EXPLORATION,
        tier=AchievementTier.BRONZE,
        condition_type="total_visits",
        condition_value=1,
        xp_reward=10,
    ),
    AchievementDefinition(
        id="visits_10",
        name="Getting Started",
        description="Visit 10 places",
        icon="🗺️",
        category=AchievementCategory.EXPLORATION,
        tier=AchievementTier.BRONZE,
        condition_type="total_visits",
        condition_value=10,
        xp_reward=50,
    ),
    AchievementDefinition(
        id="visits_50",
        name="Seasoned Explorer",
        description="Visit 50 places",
        icon="🧭",
        category=AchievementCategory.EXPLORATION,
        tier=AchievementTier.SILVER,
        condition_type="total_visits",
        condition_value=50,
        xp_reward=150,
    ),
    AchievementDefinition(
        id="visits_100",
        name="Century Club",
        description="Visit 100 places",
        icon="💯",
        category=AchievementCategory.EXPLORATION,
        tier=AchievementTier.GOLD,
        condition_type="total_visits",
        condition_value=100,
        xp_reward=500,
    ),
    AchievementDefinition(
        id="abandoned_10",
        name="Urban Explorer",
        description="Visit 10 abandoned places",
        icon="🏚️",
        category=AchievementCategory.EXPLORATION,
        tier=AchievementTier.SILVER,
        condition_type="category_abandoned",
        condition_value=10,
        xp_reward=100,
    ),
    AchievementDefinition(
        id="underground_5",
        name="Subterranean",
        description="Visit 5 underground places",
        icon="🕳️",
        category=AchievementCategory.EXPLORATION,
        tier=AchievementTier.SILVER,
        condition_type="category_underground",
        condition_value=5,
        xp_reward=100,
    ),
    AchievementDefinition(
        id="cave_3",
        name="Spelunker",
        description="Visit 3 caves",
        icon="🦇",
        category=AchievementCategory.EXPLORATION,
        tier=AchievementTier.SILVER,
        condition_type="category_cave",
        condition_value=3,
        xp_reward=100,
    ),
    AchievementDefinition(
        id="viewpoint_10",
        name="Sky Watcher",
        description="Visit 10 viewpoints",
        icon="🔭",
        category=AchievementCategory.EXPLORATION,
        tier=AchievementTier.SILVER,
        condition_type="category_viewpoint",
        condition_value=10,
        xp_reward=100,
    ),
    AchievementDefinition(
        id="ruins_5",
        name="History Hunter",
        description="Visit 5 ruins",
        icon="🏛️",
        category=AchievementCategory.EXPLORATION,
        tier=AchievementTier.SILVER,
        condition_type="category_ruins",
        condition_value=5,
        xp_reward=100,
    ),
    AchievementDefinition(
        id="military_3",
        name="Recon Specialist",
        description="Visit 3 military sites",
        icon="🎖️",
        category=AchievementCategory.EXPLORATION,
        tier=AchievementTier.SILVER,
        condition_type="category_military",
        condition_value=3,
        xp_reward=100,
    ),
    # Behavior category
    AchievementDefinition(
        id="streak_3",
        name="Three-peat",
        description="Explore 3 days in a row",
        icon="🔥",
        category=AchievementCategory.BEHAVIOR,
        tier=AchievementTier.BRONZE,
        condition_type="streak_days",
        condition_value=3,
        xp_reward=30,
    ),
    AchievementDefinition(
        id="streak_7",
        name="Week Warrior",
        description="Explore 7 days in a row",
        icon="⚡",
        category=AchievementCategory.BEHAVIOR,
        tier=AchievementTier.SILVER,
        condition_type="streak_days",
        condition_value=7,
        xp_reward=100,
    ),
    AchievementDefinition(
        id="streak_30",
        name="Month of Discovery",
        description="Explore 30 days in a row",
        icon="🌟",
        category=AchievementCategory.BEHAVIOR,
        tier=AchievementTier.GOLD,
        condition_type="streak_days",
        condition_value=30,
        xp_reward=500,
    ),
    AchievementDefinition(
        id="distance_10",
        name="Ten Klicks",
        description="Walk a total of 10 km between places",
        icon="🚶",
        category=AchievementCategory.BEHAVIOR,
        tier=AchievementTier.BRONZE,
        condition_type="total_distance_km",
        condition_value=10,
        xp_reward=30,
    ),
    AchievementDefinition(
        id="distance_100",
        name="Hundred Miles",
        description="Walk a total of 100 km between places",
        icon="🥾",
        category=AchievementCategory.BEHAVIOR,
        tier=AchievementTier.SILVER,
        condition_type="total_distance_km",
        condition_value=100,
        xp_reward=200,
    ),
    AchievementDefinition(
        id="distance_500",
        name="Long Haul",
        description="Walk a total of 500 km between places",
        icon="🏃",
        category=AchievementCategory.BEHAVIOR,
        tier=AchievementTier.GOLD,
        condition_type="total_distance_km",
        condition_value=500,
        xp_reward=500,
    ),
    AchievementDefinition(
        id="notes_10",
        name="Field Journalist",
        description="Write 10 place notes",
        icon="📝",
        category=AchievementCategory.BEHAVIOR,
        tier=AchievementTier.BRONZE,
        condition_type="total_notes",
        condition_value=10,
        xp_reward=30,
    ),
    AchievementDefinition(
        id="photos_20",
        name="Shutterbug",
        description="Attach photos to 20 notes",
        icon="📸",
        category=AchievementCategory.BEHAVIOR,
        tier=AchievementTier.SILVER,
        condition_type="total_photos",
        condition_value=20,
        xp_reward=100,
    ),
    # Surprise category (hidden)
    AchievementDefinition(
        id="night_explorer",
        name="Night Owl",
        description="Visit a place between midnight and 5 AM",
        icon="🦉",
        category=AchievementCategory.SURPRISE,
        tier=AchievementTier.SILVER,
        condition_type="night_visit",
        condition_value=1,
        xp_reward=75,
        hidden=True,
    ),
    AchievementDefinition(
        id="fog_1km",
        name="Fog Lifter",
        description="Reveal 1 km² of the fog of war",
        icon="🌫️",
        category=AchievementCategory.SURPRISE,
        tier=AchievementTier.BRONZE,
        condition_type="fog_area_km2",
        condition_value=1,
        xp_reward=50,
    ),
    AchievementDefinition(
        id="fog_10km",
        name="Cartographer",
        description="Reveal 10 km² of the fog of war",
        icon="🗺️",
        category=AchievementCategory.SURPRISE,
        tier=AchievementTier.GOLD,
        condition_type="fog_area_km2",
        condition_value=10,
        xp_reward=300,
    ),
    AchievementDefinition(
        id="early_bird",
        name="Early Bird",
        description="Visit a place before 6 AM",
        icon="🌅",
        category=AchievementCategory.SURPRISE,
        tier=AchievementTier.BRONZE,
        condition_type="early_visit",
        condition_value=1,
        xp_reward=25,
        hidden=True,
    ),
    AchievementDefinition(
        id="no_description",
        name="Into the Unknown",
        description="Visit a place that has no description",
        icon="❓",
        category=AchievementCategory.SURPRISE,
        tier=AchievementTier.SILVER,
        condition_type="no_description_visit",
        condition_value=1,
        xp_reward=50,
        hidden=True,
    ),
    # Regional category
    AchievementDefinition(
        id="region_5_places",
        name="Local Expert",
        description="Visit 5 places in the same city/region",
        icon="📍",
        category=AchievementCategory.REGIONAL,
        tier=AchievementTier.BRONZE,
        condition_type="region_places",
        condition_value=5,
        xp_reward=50,
    ),
    AchievementDefinition(
        id="region_diverse",
        name="Category Collector",
        description="Visit places from 5 different categories",
        icon="🎯",
        category=AchievementCategory.REGIONAL,
        tier=AchievementTier.SILVER,
        condition_type="unique_categories",
        condition_value=5,
        xp_reward=100,
    ),
    AchievementDefinition(
        id="multi_region",
        name="Nomad",
        description="Visit places in 3 different regions/trips",
        icon="🌍",
        category=AchievementCategory.REGIONAL,
        tier=AchievementTier.SILVER,
        condition_type="unique_trips",
        condition_value=3,
        xp_reward=100,
    ),
]

_ACHIEVEMENT_MAP: dict[str, AchievementDefinition] = {a.id: a for a in ACHIEVEMENTS}


async def check_achievements(
    visits: list[Visit],
    notes: list[PlaceNote],
    streak_days: int = 0,
    total_distance_km: float = 0.0,
) -> NewAchievementsResponse:
    """Check all achievements against current state and unlock new ones."""
    _ensure_loaded()
    newly_unlocked: list[AchievementProgress] = []
    total_xp_earned = 0

    # Compute current stats
    visited = [v for v in visits if v.status == VisitStatus.VISITED]
    total_visits = len(visited)
    fog_area = len(_fog_cells) * _CELL_AREA_KM2

    # Category counts from tags in notes
    category_counts: Counter[str] = Counter()
    for note in notes:
        for tag in note.tags:
            category_counts[tag.lower()] += 1

    # Count notes with photos
    notes_with_photos = sum(1 for n in notes if n.photos)

    # Time-based checks
    night_visits = sum(
        1 for v in visited if 0 <= v.visited_at.hour < 5
    )
    early_visits = sum(
        1 for v in visited if 5 <= v.visited_at.hour < 6
    )

    # Unique categories
    unique_cats: set[str] = set()
    for note in notes:
        for tag in note.tags:
            unique_cats.add(tag.lower())

    # Unique trips
    unique_trips = {v.trip_id for v in visited if v.trip_id}

    # Places in same trip (region proxy)
    trip_place_counts: Counter[str] = Counter()
    for v in visited:
        if v.trip_id:
            trip_place_counts[v.trip_id] += 1
    max_region_places = max(trip_place_counts.values()) if trip_place_counts else 0

    # Map condition types to current values
    condition_values: dict[str, float] = {
        "total_visits": total_visits,
        "streak_days": streak_days,
        "total_distance_km": total_distance_km,
        "total_notes": len(notes),
        "total_photos": notes_with_photos,
        "night_visit": night_visits,
        "early_visit": early_visits,
        "fog_area_km2": fog_area,
        "no_description_visit": sum(
            1 for v in visited if not v.place_name
        ),
        "unique_categories": len(unique_cats),
        "unique_trips": len(unique_trips),
        "region_places": max_region_places,
        "category_abandoned": category_counts.get("abandoned", 0),
        "category_underground": category_counts.get("underground", 0),
        "category_cave": category_counts.get("cave", 0),
        "category_viewpoint": category_counts.get("viewpoint", 0),
        "category_ruins": category_counts.get("ruins", 0),
        "category_military": category_counts.get("military", 0),
    }

    async with _write_lock:
        for ach_def in ACHIEVEMENTS:
            current = condition_values.get(ach_def.condition_type, 0)
            completed = current >= ach_def.condition_value
            existing = _achievements.get(ach_def.id)

            if completed and (not existing or not existing.completed):
                now = datetime.now(UTC)
                _achievements[ach_def.id] = UserAchievement(
                    achievement_id=ach_def.id,
                    unlocked_at=now,
                    progress=1.0,
                    completed=True,
                    notified=False,
                )
                total_xp_earned += ach_def.xp_reward
                newly_unlocked.append(AchievementProgress(
                    definition=ach_def,
                    current_value=current,
                    target_value=ach_def.condition_value,
                    progress_percent=100.0,
                    completed=True,
                    unlocked_at=now,
                ))
            elif not completed and not existing:
                progress = (current / ach_def.condition_value * 100) if ach_def.condition_value > 0 else 0
                _achievements[ach_def.id] = UserAchievement(
                    achievement_id=ach_def.id,
                    progress=min(current / ach_def.condition_value, 0.99) if ach_def.condition_value > 0 else 0,
                    completed=False,
                )

        if newly_unlocked:
            _save_achievements()

    # Award XP for newly unlocked achievements
    if total_xp_earned > 0:
        await _award_xp(total_xp_earned, "achievement_unlock", "Achievement rewards")

    return NewAchievementsResponse(
        new_achievements=newly_unlocked,
        xp_earned=total_xp_earned,
    )


async def get_achievements() -> tuple[list[AchievementProgress], int, int]:
    """Get all achievements with progress."""
    _ensure_loaded()
    result: list[AchievementProgress] = []

    for ach_def in ACHIEVEMENTS:
        user_ach = _achievements.get(ach_def.id)
        if user_ach and user_ach.completed:
            result.append(AchievementProgress(
                definition=ach_def,
                current_value=ach_def.condition_value,
                target_value=ach_def.condition_value,
                progress_percent=100.0,
                completed=True,
                unlocked_at=user_ach.unlocked_at,
            ))
        elif user_ach:
            current = user_ach.progress * ach_def.condition_value
            result.append(AchievementProgress(
                definition=ach_def,
                current_value=current,
                target_value=ach_def.condition_value,
                progress_percent=round(user_ach.progress * 100, 1),
                completed=False,
            ))
        else:
            # Hidden achievements show as hidden until progress starts
            if ach_def.hidden:
                result.append(AchievementProgress(
                    definition=AchievementDefinition(
                        id=ach_def.id,
                        name="???",
                        description="Hidden achievement — keep exploring!",
                        icon="🔒",
                        category=ach_def.category,
                        tier=ach_def.tier,
                        condition_type=ach_def.condition_type,
                        condition_value=ach_def.condition_value,
                        xp_reward=ach_def.xp_reward,
                        hidden=True,
                    ),
                    current_value=0,
                    target_value=ach_def.condition_value,
                    progress_percent=0,
                    completed=False,
                ))
            else:
                result.append(AchievementProgress(
                    definition=ach_def,
                    current_value=0,
                    target_value=ach_def.condition_value,
                    progress_percent=0,
                    completed=False,
                ))

    unlocked = sum(1 for r in result if r.completed)
    return result, unlocked, len(ACHIEVEMENTS)


# ── Story 6.3: Explorer Level ───────────────────────────────────

LEVEL_THRESHOLDS: list[LevelInfo] = [
    LevelInfo(
        level=ExplorerLevel.NOVICE,
        min_xp=0,
        max_xp=99,
        unlocked_features=["basic_map", "visit_tracking"],
    ),
    LevelInfo(
        level=ExplorerLevel.SCOUT,
        min_xp=100,
        max_xp=499,
        unlocked_features=["category_filters", "basic_stats"],
    ),
    LevelInfo(
        level=ExplorerLevel.EXPLORER,
        min_xp=500,
        max_xp=1499,
        unlocked_features=["advanced_filters", "route_builder"],
    ),
    LevelInfo(
        level=ExplorerLevel.PATHFINDER,
        min_xp=1500,
        max_xp=3999,
        unlocked_features=["llm_descriptions", "trip_export"],
    ),
    LevelInfo(
        level=ExplorerLevel.TRAILBLAZER,
        min_xp=4000,
        max_xp=9999,
        unlocked_features=["ai_recommendations", "early_access_sources"],
    ),
    LevelInfo(
        level=ExplorerLevel.LEGEND,
        min_xp=10000,
        max_xp=None,
        unlocked_features=["all_features", "custom_themes", "api_access"],
    ),
]


def _get_level(xp: int) -> tuple[ExplorerLevel, LevelInfo]:
    """Determine level from XP."""
    for info in reversed(LEVEL_THRESHOLDS):
        if xp >= info.min_xp:
            return info.level, info
    return ExplorerLevel.NOVICE, LEVEL_THRESHOLDS[0]


def _get_title(level: ExplorerLevel) -> str:
    titles = {
        ExplorerLevel.NOVICE: "Novice Explorer",
        ExplorerLevel.SCOUT: "Scout",
        ExplorerLevel.EXPLORER: "Explorer",
        ExplorerLevel.PATHFINDER: "Pathfinder",
        ExplorerLevel.TRAILBLAZER: "Trailblazer",
        ExplorerLevel.LEGEND: "Living Legend",
    }
    return titles.get(level, "Explorer")


# XP rewards for different actions
XP_REWARDS = {
    "visit_new": 10,
    "visit_rare": 25,       # rare category (abandoned, underground, cave)
    "visit_confirmed": 15,  # confirmed with note
    "note_with_text": 5,
    "note_with_photo": 8,
    "note_with_rating": 3,
    "fog_reveal_100": 5,    # per 100 new cells revealed
    "streak_bonus": 10,     # per day streak multiplier
}

_RARE_CATEGORIES = {"abandoned", "underground", "cave", "military", "ruins"}


async def _award_xp(xp: int, source: str, description: str, visit_id: str | None = None) -> None:
    """Internal: award XP and save."""
    global _total_xp
    event = XpEvent(
        id=f"xp_{uuid.uuid4().hex[:12]}",
        source=source,
        xp=xp,
        description=description,
        visit_id=visit_id,
    )
    _total_xp += xp
    _xp_events.append(event)
    _save_xp()
    logger.info("Awarded %d XP for %s (total: %d)", xp, source, _total_xp)


async def award_visit_xp(
    visit: Visit,
    notes: list[PlaceNote],
    is_rare_category: bool = False,
    streak_days: int = 0,
) -> XpEventResponse:
    """Award XP for a visit with notes, considering rarity and streaks."""
    _ensure_loaded()
    total_earned = 0
    old_level, _ = _get_level(_total_xp)

    # Base visit XP
    base_xp = XP_REWARDS["visit_new"]
    if is_rare_category:
        base_xp = XP_REWARDS["visit_rare"]
    total_earned += base_xp
    await _award_xp(base_xp, "visit", f"Visited {visit.place_name or visit.place_id}", visit.id)

    # Note bonuses
    for note in notes:
        if note.text:
            await _award_xp(XP_REWARDS["note_with_text"], "note_text", "Note with text", visit.id)
            total_earned += XP_REWARDS["note_with_text"]
        if note.photos:
            await _award_xp(XP_REWARDS["note_with_photo"], "note_photo", "Note with photo", visit.id)
            total_earned += XP_REWARDS["note_with_photo"]
        if note.rating.average > 0:
            await _award_xp(XP_REWARDS["note_with_rating"], "note_rating", "Note with rating", visit.id)
            total_earned += XP_REWARDS["note_with_rating"]

    # Streak bonus
    if streak_days > 1:
        streak_xp = min(streak_days, 30) * XP_REWARDS["streak_bonus"]
        await _award_xp(streak_xp, "streak", f"{streak_days}-day streak bonus", visit.id)
        total_earned += streak_xp

    new_level, new_info = _get_level(_total_xp)
    leveled_up = new_level != old_level

    return XpEventResponse(
        xp_earned=total_earned,
        total_xp=_total_xp,
        level=new_level,
        leveled_up=leveled_up,
        new_level=new_level if leveled_up else None,
    )


async def get_explorer_profile() -> ExplorerProfile:
    """Get the explorer's gamification profile."""
    _ensure_loaded()
    level, level_info = _get_level(_total_xp)

    # Calculate XP to next level
    next_level_idx = None
    for i, info in enumerate(LEVEL_THRESHOLDS):
        if info.level == level and i + 1 < len(LEVEL_THRESHOLDS):
            next_level_idx = i + 1
            break

    xp_to_next = 0
    progress = 100.0
    if next_level_idx is not None:
        next_info = LEVEL_THRESHOLDS[next_level_idx]
        xp_to_next = next_info.min_xp - _total_xp
        level_range = next_info.min_xp - level_info.min_xp
        current_progress = _total_xp - level_info.min_xp
        progress = round(current_progress / level_range * 100, 1) if level_range > 0 else 100.0

    achievements_unlocked = sum(1 for a in _achievements.values() if a.completed)
    fog_area = len(_fog_cells) * _CELL_AREA_KM2

    return ExplorerProfile(
        total_xp=_total_xp,
        level=level,
        level_info=level_info,
        xp_to_next_level=max(0, xp_to_next),
        level_progress_percent=progress,
        achievements_unlocked=achievements_unlocked,
        total_achievements=len(ACHIEVEMENTS),
        fog_explored_km2=round(fog_area, 4),
        title=_get_title(level),
    )


async def get_xp_history(limit: int = 50, offset: int = 0) -> tuple[list[XpEvent], int]:
    """Get XP event history."""
    _ensure_loaded()
    events = sorted(_xp_events, key=lambda e: e.created_at, reverse=True)
    total = len(events)
    return events[offset : offset + limit], total


async def get_leaderboard() -> list[LeaderboardEntry]:
    """Get leaderboard (single user for now)."""
    _ensure_loaded()
    level, _ = _get_level(_total_xp)
    fog_area = len(_fog_cells) * _CELL_AREA_KM2
    achievements_count = sum(1 for a in _achievements.values() if a.completed)

    return [
        LeaderboardEntry(
            user_id="self",
            username="You",
            total_xp=_total_xp,
            level=level,
            explored_km2=round(fog_area, 4),
            achievements_count=achievements_count,
        )
    ]


# ── Integration: on_visit hook ───────────────────────────────────


async def on_visit_created(
    visit: Visit,
    notes: list[PlaceNote],
    all_visits: list[Visit],
    all_notes: list[PlaceNote],
    streak_days: int = 0,
    total_distance_km: float = 0.0,
) -> dict:
    """Hook called when a new visit is created. Awards XP, checks achievements, reveals fog."""
    results = {}

    # 1. Reveal fog around the visit location
    fog_result = await reveal_fog(
        [(visit.coordinates.lat, visit.coordinates.lng, visit.visited_at)],
        radius_m=50.0,
        visit_id=visit.id,
    )
    results["fog"] = fog_result

    # 2. Award XP
    is_rare = any(
        tag in _RARE_CATEGORIES for note in notes for tag in note.tags
    )
    xp_result = await award_visit_xp(
        visit=visit,
        notes=notes,
        is_rare_category=is_rare,
        streak_days=streak_days,
    )
    results["xp"] = xp_result

    # 3. Check achievements
    ach_result = await check_achievements(
        visits=all_visits,
        notes=all_notes,
        streak_days=streak_days,
        total_distance_km=total_distance_km,
    )
    results["achievements"] = ach_result

    return results


# ── Test helper ──────────────────────────────────────────────────


def _reset_store() -> None:
    """Reset in-memory stores (for testing only)."""
    global _data_dir, _total_xp
    _fog_cells.clear()
    _achievements.clear()
    _xp_events.clear()
    _total_xp = 0
    import tempfile
    _data_dir = Path(tempfile.mkdtemp())
