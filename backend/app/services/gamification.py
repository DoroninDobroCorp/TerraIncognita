"""Gamification service — business logic for Epic 6.

Module organisation:
  - Fog of War: GPS trail → grid cells → coverage stats
  - Achievements: definitions, checking, unlocking
  - Explorer Level: XP earning, level progression, feature unlocks
  - Persistence: JSON-file backed (same pattern as journal)

Thread safety: atomic writes via temp-file-then-rename.
Concurrency: single event loop; the in-memory mutations run without awaiting, so they are
atomic with respect to other coroutines (do not put an ``await`` inside a read-modify-write).
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
from app.utils.atomic_io import (
    atomic_write_text,
    load_json_file,
    load_model_records,
    quarantine_corrupt,
    scratch_dir,
)
from app.utils.geo import haversine_distance_m as _haversine_m

logger = logging.getLogger(__name__)

# ── In-memory store (JSON-file backed) ───────────────────────────

_fog_cells: dict[str, FogCell] = {}  # key: "lat_lng" grid key
_achievements: dict[str, UserAchievement] = {}  # key: achievement_id
_xp_events: list[XpEvent] = []
_total_xp: int = 0
# De-duplication keys of XP that was already paid ("visit:<place>", "streak:<day>", ...).
# Kept separately from the (rotating) event log so old rewards cannot be re-farmed.
_xp_refs: dict[str, None] = {}
_fog_area_km2: float = 0.0
_data_dir: Path | None = None
_loaded = False

# Resource limits: every save rewrites the whole store file.
MAX_FOG_CELLS = 100_000  # ~1,200 km2 of explored ground
MAX_XP_EVENTS = 2_000
MAX_XP_REFS = 100_000
_write_lock = asyncio.Lock()


def _get_data_dir() -> Path:
    global _data_dir
    if _data_dir is None:
        _data_dir = Path(settings.gamification_data_dir)
        _data_dir.mkdir(parents=True, exist_ok=True)
    return _data_dir


def _atomic_write(path: Path, data: str) -> None:
    atomic_write_text(path, data)


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
        "refs": list(_xp_refs),
    }
    _atomic_write(path, json.dumps(data, default=str, ensure_ascii=False))


def _load_store() -> None:
    """Load persisted data once (first access); unreadable data is backed up, never dropped.

    Everything is read into locals first and the flag is set last: a file that cannot be read makes
    this raise on every call instead of leaving a half-loaded store (e.g. total XP 0) that the next
    save would write over the files that were never read.
    """
    global _total_xp, _loaded, _fog_area_km2
    if _loaded:
        return
    data_dir = _get_data_dir()

    fog = load_model_records(data_dir / "fog_cells.json", FogCell)
    achievements = load_model_records(data_dir / "achievements.json", UserAchievement)

    events: list[XpEvent] = []
    refs: dict[str, None] = {}
    total_xp = 0
    xp_path = data_dir / "xp_events.json"
    raw = load_json_file(xp_path)
    if isinstance(raw, dict):
        bad = 0
        for e in raw.get("events", []):
            try:
                events.append(XpEvent.model_validate(e))
            except Exception:
                bad += 1
        stored_refs = raw.get("refs", [])
        if isinstance(stored_refs, list):
            for r in stored_refs:
                if isinstance(r, str):
                    refs[r] = None
        for e in events:
            if e.ref:
                refs[e.ref] = None
        total = raw.get("total_xp", 0)
        total_xp = total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else 0
        if bad or total != total_xp:
            quarantine_corrupt(xp_path, f"{bad} invalid event(s) / total_xp={total!r}", keep_original=True)
    elif raw is not None:
        quarantine_corrupt(xp_path, "top-level JSON value is not an object")

    _fog_cells.update(fog)
    _fog_area_km2 = sum(_cell_area_km2(c.lat) for c in _fog_cells.values())
    _achievements.update(achievements)
    _xp_events.extend(events)
    _xp_refs.update(refs)
    _total_xp = total_xp
    _loaded = True


def _ensure_loaded() -> None:
    _load_store()


# ── Grid utilities ───────────────────────────────────────────────

# Cell size: 0.001 degrees = ~111 m of latitude (less of longitude away from the equator)
_CELL_SIZE = 0.001
_EARTH_RADIUS_M = 6_371_000.0
_M_PER_DEG = _EARTH_RADIUS_M * math.pi / 180.0  # ~111.195 km per degree of latitude
# Equatorial cell area in km2 (0.111 km x 0.111 km). NOT 0.0111: that is 11 m, 100x too small.
_CELL_AREA_KM2 = (_CELL_SIZE * _M_PER_DEG / 1000.0) ** 2
# Longitude steps per side are clamped: near the poles a degree of longitude is tiny and an
# unclamped disc has thousands of cells per row (60 covers a 500 m radius up to ~85 degrees).
_MAX_LNG_STEPS = 60


def _cell_area_km2(lat: float) -> float:
    """Area of one grid cell at ``lat`` (cells shrink east-west with cos(lat))."""
    return _CELL_AREA_KM2 * max(math.cos(math.radians(lat)), 0.0)


def _cell_key(lat: float, lng: float) -> str:
    """Quantize coordinates to grid cell key (longitude wrapped to [-180, 180))."""
    lng = ((lng + 180.0) % 360.0) - 180.0
    grid_lat = round(lat / _CELL_SIZE) * _CELL_SIZE
    grid_lng = round(lng / _CELL_SIZE) * _CELL_SIZE
    return f"{grid_lat:.4f}_{grid_lng:.4f}"


def _cell_coords(key: str) -> tuple[float, float]:
    parts = key.split("_")
    return float(parts[0]), float(parts[1])


def _row_lng_steps(lat: float, row_lat: float, radius_m: float) -> int:
    """Cells to each side (east/west) of the centre that are within ``radius_m`` on ``row_lat``.

    Exact inverse of the haversine formula for a fixed latitude row, so the cost is O(1)
    per row instead of a haversine call per candidate cell. -1 means the row is out of reach.
    """
    a_max = math.sin(radius_m / (2 * _EARTH_RADIUS_M)) ** 2
    a_row = math.sin(math.radians(row_lat - lat) / 2) ** 2
    if a_row > a_max:
        return -1
    denom = math.cos(math.radians(lat)) * math.cos(math.radians(row_lat))
    if denom <= 1e-12:
        return _MAX_LNG_STEPS
    rem = (a_max - a_row) / denom
    if rem >= 1.0:
        return _MAX_LNG_STEPS
    half_span_deg = math.degrees(2 * math.asin(math.sqrt(rem)))
    return min(int(half_span_deg / _CELL_SIZE + 1e-9), _MAX_LNG_STEPS)


def _lat_rows(lat: float, radius_m: float):
    """Yield (row latitude, east/west steps) for every grid row within reach."""
    lat_steps = int(radius_m / (_M_PER_DEG * _CELL_SIZE)) + 1
    for dlat in range(-lat_steps, lat_steps + 1):
        row_lat = lat + dlat * _CELL_SIZE
        if not -90.0 <= row_lat <= 90.0:
            continue
        n = _row_lng_steps(lat, row_lat, radius_m)
        if n >= 0:
            yield row_lat, n


def _cells_in_radius(lat: float, lng: float, radius_m: float) -> list[str]:
    """Get all grid cell keys within radius of a point."""
    cells = []
    for row_lat, n in _lat_rows(lat, radius_m):
        for dlng in range(-n, n + 1):
            cells.append(_cell_key(row_lat, lng + dlng * _CELL_SIZE))
    return cells


def _count_cells_in_radius(lat: float, radius_m: float) -> int:
    """Number of cells :func:`_cells_in_radius` would return, without building them."""
    return sum(2 * n + 1 for _, n in _lat_rows(lat, radius_m))


# ── Story 6.1: Fog of War ───────────────────────────────────────


async def reveal_fog(
    points: list[tuple[float, float, datetime | None]],
    radius_m: float = 50.0,
    visit_id: str | None = None,
) -> FogRevealResponse:
    """Reveal fog of war cells from GPS trail points."""
    global _fog_area_km2
    _ensure_loaded()
    new_count = 0
    capped = False

    async with _write_lock:
        for lat, lng, ts in points:
            if len(_fog_cells) >= MAX_FOG_CELLS:
                logger.warning("Fog store is full (%d cells); not revealing more", MAX_FOG_CELLS)
                capped = True
                break
            for key in _cells_in_radius(lat, lng, radius_m):
                if key in _fog_cells:
                    continue
                if len(_fog_cells) >= MAX_FOG_CELLS:
                    capped = True
                    break
                clat, clng = _cell_coords(key)
                _fog_cells[key] = FogCell(
                    lat=clat,
                    lng=clng,
                    explored_at=ts or datetime.now(UTC),
                    visit_id=visit_id,
                )
                _fog_area_km2 += _cell_area_km2(clat)
                new_count += 1

        if new_count:
            _save_fog()

    total = len(_fog_cells)
    area = _fog_area_km2

    logger.info("Fog revealed: %d new cells, %d total (%.3f km²)", new_count, total, area)
    return FogRevealResponse(
        new_cells_revealed=new_count,
        total_explored_cells=total,
        total_explored_area_km2=round(area, 4),
        capped=capped,
    )


async def get_fog_status() -> FogOfWarState:
    """Get current fog of war state."""
    _ensure_loaded()
    total = len(_fog_cells)

    return FogOfWarState(
        total_explored_cells=total,
        total_explored_area_km2=round(_fog_area_km2, 4),
        gps_trail=list(_fog_cells.values())[-100:],  # last 100 cells
    )


async def get_fog_region(
    lat: float, lng: float, radius_km: float, region_name: str = "custom"
) -> FogRegionStats:
    """Get fog coverage for a specific region."""
    _ensure_loaded()
    radius_m = radius_km * 1000

    # Total cells in the region (counted row by row; building 800k keys for a 50 km radius
    # blocked the event loop for seconds)
    total_cells = _count_cells_in_radius(lat, radius_m)

    # Count explored cells in region
    explored = 0
    for cell in _fog_cells.values():
        dist = _haversine_m(lat, lng, cell.lat, cell.lng)
        if dist <= radius_m:
            explored += 1

    coverage = min(explored / total_cells * 100, 100.0) if total_cells > 0 else 0.0
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


def _local_hour(visit: Visit) -> int:
    """Hour of day at the visit's location (UTC shifted by the longitude's mean solar time).

    ``visited_at`` is stamped in UTC; judging "between midnight and 5 AM" on the UTC hour
    unlocks Night Owl for the wrong people. No time-zone database is needed for a
    15-degrees-per-hour approximation.
    """
    when = visit.visited_at
    when = when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)
    return (when + timedelta(hours=visit.coordinates.lng / 15.0)).hour


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
    # Notes on places that were only planned or skipped are not evidence of having been anywhere.
    visited_ids = {v.id for v in visited}
    notes = [n for n in notes if n.visit_id in visited_ids]
    # Places, not check-ins: ten check-ins at one place are one place.
    total_visits = len({v.place_id for v in visited})
    fog_area = _fog_area_km2

    # Category counts from note tags, counted once per *place* (ten notes on one place that
    # are all tagged "abandoned" are one abandoned place)
    category_places: dict[str, set[str]] = {}
    for note in notes:
        for tag in note.tags:
            category_places.setdefault(tag.lower(), set()).add(note.place_id)
    category_counts: Counter[str] = Counter({t: len(p) for t, p in category_places.items()})

    # Count notes with photos
    notes_with_photos = sum(1 for n in notes if n.photos)

    # Time-based checks, in the explorer's local (mean solar) time
    night_visits = sum(1 for v in visited if 0 <= _local_hour(v) < 5)
    early_visits = sum(1 for v in visited if 5 <= _local_hour(v) < 6)

    # Unique categories
    unique_cats: set[str] = set(category_places)

    # Unique trips
    unique_trips = {v.trip_id for v in visited if v.trip_id}

    # Distinct places in the same trip (region proxy)
    trip_places: dict[str, set[str]] = {}
    for v in visited:
        if v.trip_id:
            trip_places.setdefault(v.trip_id, set()).add(v.place_id)
    max_region_places = max((len(p) for p in trip_places.values()), default=0)

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

    progress_changed = False
    unlocked_defs: list[AchievementDefinition] = []
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
                unlocked_defs.append(ach_def)
                newly_unlocked.append(AchievementProgress(
                    definition=ach_def,
                    current_value=current,
                    target_value=ach_def.condition_value,
                    progress_percent=100.0,
                    completed=True,
                    unlocked_at=now,
                ))
            elif not completed:
                progress = (
                    min(current / ach_def.condition_value, 0.99) if ach_def.condition_value > 0 else 0
                )
                if not existing:
                    _achievements[ach_def.id] = UserAchievement(
                        achievement_id=ach_def.id, progress=progress, completed=False,
                    )
                    progress_changed = True
                elif existing.progress != progress:
                    # Progress used to be written once (first check) and never refreshed.
                    existing.progress = progress
                    progress_changed = True

        if newly_unlocked or progress_changed:
            _save_achievements()

    # Award XP for newly unlocked achievements: one history entry per achievement, each paid
    # at most once ever (if the achievement store is lost or skipped as corrupt, everything
    # unlocks again but the rewards are not repaid)
    for ach_def in unlocked_defs:
        if await _award_xp(
            ach_def.xp_reward, "achievement_unlock", f"Achievement: {ach_def.name}",
            ref=f"achievement:{ach_def.id}",
        ):
            total_xp_earned += ach_def.xp_reward

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
        if ach_def.hidden and not (user_ach and user_ach.completed):
            # Hidden until unlocked, even after progress records exist for every achievement
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
        elif user_ach and user_ach.completed:
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


async def _award_xp(
    xp: int,
    source: str,
    description: str,
    visit_id: str | None = None,
    ref: str | None = None,
) -> bool:
    """Internal: award XP and save. Returns False when ``ref`` was already paid.

    ``ref`` is an idempotency key ("visit:<place_id>", "streak:<day>", "note_text:<place_id>"):
    the same reward can never be granted twice by repeating a request.
    """
    global _total_xp
    _ensure_loaded()  # never append to (and then rewrite) a store that was not read yet
    if ref is not None and ref in _xp_refs:
        return False
    event = XpEvent(
        id=f"xp_{uuid.uuid4().hex[:12]}",
        source=source,
        xp=xp,
        description=description,
        visit_id=visit_id,
        ref=ref,
    )
    _total_xp += xp
    _xp_events.append(event)
    if len(_xp_events) > MAX_XP_EVENTS:  # the log rotates, _total_xp does not
        del _xp_events[: len(_xp_events) - MAX_XP_EVENTS]
    if ref is not None:
        _xp_refs[ref] = None
        while len(_xp_refs) > MAX_XP_REFS:
            del _xp_refs[next(iter(_xp_refs))]
    _save_xp()
    logger.info("Awarded %d XP for %s (total: %d)", xp, source, _total_xp)
    return True


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

    # Base visit XP: once per place, however often the place is checked in
    base_xp = XP_REWARDS["visit_new"]
    if is_rare_category:
        base_xp = XP_REWARDS["visit_rare"]
    if await _award_xp(
        base_xp, "visit", f"Visited {visit.place_name or visit.place_id}", visit.id,
        ref=f"visit:{visit.place_id}",
    ):
        total_earned += base_xp

    # Note bonuses: once per place and kind
    for note in notes:
        for flag, key, source, text in (
            (bool(note.text), "note_with_text", "note_text", "Note with text"),
            (bool(note.photos), "note_with_photo", "note_photo", "Note with photo"),
            (note.rating.average > 0, "note_with_rating", "note_rating", "Note with rating"),
        ):
            # once per place and kind — keyed on the place, not the note, so that writing and deleting
            # notes over and over cannot pay the same bonus again
            if flag and await _award_xp(
                XP_REWARDS[key], source, text, visit.id, ref=f"{source}:{visit.place_id}"
            ):
                total_earned += XP_REWARDS[key]

    # Streak bonus: once per day (it used to be paid again on every single check-in)
    if streak_days > 1:
        streak_xp = min(streak_days, 30) * XP_REWARDS["streak_bonus"]
        today = datetime.now(UTC).date().isoformat()
        if await _award_xp(
            streak_xp, "streak", f"{streak_days}-day streak bonus", visit.id,
            ref=f"streak:{today}",
        ):
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
    fog_area = _fog_area_km2

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
    # The log is append-only, so reversing it is "newest first" even when timestamps tie
    # (sorting by created_at returned equal timestamps oldest first).
    events = list(reversed(_xp_events))
    total = len(events)
    return events[offset : offset + limit], total


async def get_leaderboard() -> list[LeaderboardEntry]:
    """Get leaderboard (single user for now)."""
    _ensure_loaded()
    level, _ = _get_level(_total_xp)
    fog_area = _fog_area_km2
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
    global _data_dir, _total_xp, _loaded, _fog_area_km2
    _loaded = False
    _fog_cells.clear()
    _achievements.clear()
    _xp_events.clear()
    _xp_refs.clear()
    _total_xp = 0
    _fog_area_km2 = 0.0
    _data_dir = scratch_dir()
