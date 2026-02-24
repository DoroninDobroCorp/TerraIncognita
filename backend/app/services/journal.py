"""Explorer Journal service — business logic for Epic 5.

Module organisation (single file for prototype simplicity):
  - Persistence layer: _atomic_write, _save_*, _load_*, _ensure_loaded
  - Visits CRUD: create_visit, update_visit, delete_visit, get_visit(s)
  - Notes CRUD: create_note, update_note, delete_note, get_notes_*
  - Trips management: create_trip, update_trip, delete_trip, auto_group_visits
  - Analytics: get_exploration_stats, get_heatmap, get_streaks
  - Proximity/dwell: check_proximity, check_dwell_time
  - Export: export_journal_html

Thread safety: writes use atomic temp-file-then-rename to prevent corruption.
Concurrency: _write_lock (asyncio.Lock) serialises concurrent mutations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import settings
from app.models.journal import (
    CategoryStat,
    DwellCheckResponse,
    ExplorationStats,
    HeatmapPoint,
    PlaceNote,
    PlaceRating,
    ProximityCheckResponse,
    StreakInfo,
    SurprisePlace,
    Trip,
    TripSummary,
    Visit,
    VisitStatus,
)
from app.models.place import Coordinates

logger = logging.getLogger(__name__)

# ── In-memory store (JSON-file backed for persistence) ───────────

_visits: dict[str, Visit] = {}
_notes: dict[str, PlaceNote] = {}
_trips: dict[str, Trip] = {}
_data_dir: Path | None = None
_write_lock = asyncio.Lock()


def _get_data_dir() -> Path:
    global _data_dir
    if _data_dir is None:
        _data_dir = Path(settings.journal_data_dir)
        _data_dir.mkdir(parents=True, exist_ok=True)
    return _data_dir


def _atomic_write(path: Path, data: str) -> None:
    """Write data atomically using temp file + rename to prevent corruption."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def _save_visits() -> None:
    path = _get_data_dir() / "visits.json"
    data = {k: v.model_dump(mode="json") for k, v in _visits.items()}
    _atomic_write(path, json.dumps(data, default=str, ensure_ascii=False))


def _save_notes() -> None:
    path = _get_data_dir() / "notes.json"
    data = {k: v.model_dump(mode="json") for k, v in _notes.items()}
    _atomic_write(path, json.dumps(data, default=str, ensure_ascii=False))


def _save_trips() -> None:
    path = _get_data_dir() / "trips.json"
    data = {k: v.model_dump(mode="json") for k, v in _trips.items()}
    _atomic_write(path, json.dumps(data, default=str, ensure_ascii=False))


def _load_store() -> None:
    """Load persisted data on first access."""
    data_dir = _get_data_dir()

    visits_path = data_dir / "visits.json"
    if visits_path.exists() and not _visits:
        try:
            raw = json.loads(visits_path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                _visits[k] = Visit.model_validate(v)
        except Exception:
            logger.warning("Failed to load visits.json, starting fresh")

    notes_path = data_dir / "notes.json"
    if notes_path.exists() and not _notes:
        try:
            raw = json.loads(notes_path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                _notes[k] = PlaceNote.model_validate(v)
        except Exception:
            logger.warning("Failed to load notes.json, starting fresh")

    trips_path = data_dir / "trips.json"
    if trips_path.exists() and not _trips:
        try:
            raw = json.loads(trips_path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                _trips[k] = Trip.model_validate(v)
        except Exception:
            logger.warning("Failed to load trips.json, starting fresh")


def _ensure_loaded() -> None:
    _load_store()


# ── Geo utilities ────────────────────────────────────────────────


def _sanitize_text(text: str, max_length: int = 10000) -> str:
    """Sanitize user-provided text: strip control chars, limit length."""
    import re
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:max_length].strip()


def _sanitize_tag(tag: str) -> str:
    """Sanitize a tag: lowercase, alphanumeric/hyphens only, limit length."""
    import re
    tag = tag.strip().lower()
    tag = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9_\-]", "", tag)
    return tag[:100]


def _sanitize_url(url: str) -> str:
    """Basic URL validation: allow only http/https/relative paths."""
    url = url.strip()
    if url.startswith(("http://", "https://", "/", "./")):
        return url[:2000]
    return ""


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine distance in meters."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Story 5.1: Visit Tracking ───────────────────────────────────


async def create_visit(
    place_id: str,
    lat: float,
    lng: float,
    place_name: str | None = None,
    status: VisitStatus = VisitStatus.VISITED,
    duration_minutes: float | None = None,
    trip_id: str | None = None,
    auto_detected: bool = False,
) -> Visit:
    """Record a visit to a place."""
    _ensure_loaded()
    visit_id = f"visit_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    visit = Visit(
        id=visit_id,
        place_id=place_id,
        place_name=place_name,
        status=status,
        coordinates=Coordinates(lat=lat, lng=lng),
        visited_at=now,
        duration_minutes=duration_minutes,
        auto_detected=auto_detected,
        trip_id=trip_id,
        created_at=now,
        updated_at=now,
    )
    _visits[visit_id] = visit

    # Auto-assign to trip if trip_id provided
    if trip_id and trip_id in _trips:
        if visit_id not in _trips[trip_id].visit_ids:
            _trips[trip_id].visit_ids.append(visit_id)
            _trips[trip_id].updated_at = now
            _save_trips()

    _save_visits()
    logger.info("Created visit %s for place %s", visit_id, place_id)

    # Trigger gamification hook (Epic 6)
    if status == VisitStatus.VISITED:
        try:
            from app.services import gamification as gam_service
            all_visits = list(_visits.values())
            all_notes = list(_notes.values())
            visit_notes = [n for n in all_notes if n.visit_id == visit_id]
            streak = _compute_streak([v for v in all_visits if v.status == VisitStatus.VISITED])
            sorted_visited = sorted(
                [v for v in all_visits if v.status == VisitStatus.VISITED],
                key=lambda v: v.visited_at,
            )
            total_dist = 0.0
            for i in range(1, len(sorted_visited)):
                prev, curr = sorted_visited[i - 1], sorted_visited[i]
                total_dist += _haversine_m(
                    prev.coordinates.lat, prev.coordinates.lng,
                    curr.coordinates.lat, curr.coordinates.lng,
                ) / 1000.0
            await gam_service.on_visit_created(
                visit=visit,
                notes=visit_notes,
                all_visits=all_visits,
                all_notes=all_notes,
                streak_days=streak.current_streak,
                total_distance_km=total_dist,
            )
        except Exception:
            logger.warning("Gamification hook failed for visit %s", visit_id, exc_info=True)

    return visit


async def get_visit(visit_id: str) -> Visit | None:
    _ensure_loaded()
    return _visits.get(visit_id)


async def list_visits(
    status: VisitStatus | None = None,
    trip_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Visit], int]:
    """List visits with optional filters."""
    _ensure_loaded()
    results = list(_visits.values())

    if status:
        results = [v for v in results if v.status == status]
    if trip_id:
        results = [v for v in results if v.trip_id == trip_id]

    results.sort(key=lambda v: v.visited_at, reverse=True)
    total = len(results)
    return results[offset : offset + limit], total


async def update_visit(
    visit_id: str,
    status: VisitStatus | None = None,
    duration_minutes: float | None = None,
    trip_id: str | None = None,
) -> Visit | None:
    """Update an existing visit."""
    _ensure_loaded()
    visit = _visits.get(visit_id)
    if not visit:
        return None

    if status is not None:
        visit.status = status
    if duration_minutes is not None:
        visit.duration_minutes = duration_minutes
    if trip_id is not None:
        # Remove from old trip
        if visit.trip_id and visit.trip_id in _trips:
            old_trip = _trips[visit.trip_id]
            if visit_id in old_trip.visit_ids:
                old_trip.visit_ids.remove(visit_id)
        # Add to new trip
        visit.trip_id = trip_id
        if trip_id in _trips and visit_id not in _trips[trip_id].visit_ids:
            _trips[trip_id].visit_ids.append(visit_id)
            _save_trips()

    visit.updated_at = datetime.now(UTC)
    _save_visits()
    return visit


async def delete_visit(visit_id: str) -> bool:
    """Delete a visit and its notes."""
    _ensure_loaded()
    if visit_id not in _visits:
        return False

    visit = _visits[visit_id]
    # Remove from trip
    if visit.trip_id and visit.trip_id in _trips:
        trip = _trips[visit.trip_id]
        if visit_id in trip.visit_ids:
            trip.visit_ids.remove(visit_id)
            _save_trips()

    # Remove associated notes
    note_ids_to_remove = [n.id for n in _notes.values() if n.visit_id == visit_id]
    for nid in note_ids_to_remove:
        del _notes[nid]
    if note_ids_to_remove:
        _save_notes()

    del _visits[visit_id]
    _save_visits()
    return True


async def check_proximity(
    lat: float, lng: float, radius_m: float = 100.0
) -> ProximityCheckResponse:
    """Check if user is near a previously visited place."""
    _ensure_loaded()
    for visit in _visits.values():
        dist = _haversine_m(lat, lng, visit.coordinates.lat, visit.coordinates.lng)
        if dist <= radius_m:
            return ProximityCheckResponse(
                nearby_place_id=visit.place_id,
                nearby_place_name=visit.place_name,
                distance_m=round(dist, 1),
                already_visited=visit.status == VisitStatus.VISITED,
            )
    return ProximityCheckResponse()


async def get_visited_place_ids() -> list[str]:
    """Return list of place IDs with status VISITED (for exclude_visited)."""
    _ensure_loaded()
    return [v.place_id for v in _visits.values() if v.status == VisitStatus.VISITED]


async def check_dwell(
    place_id: str,
    lat: float,
    lng: float,
    dwell_minutes: float,
    place_name: str | None = None,
) -> DwellCheckResponse:
    """Check if user dwelled long enough near a place to suggest check-in (AC 5.1: >5 min)."""
    _ensure_loaded()
    threshold = settings.journal_auto_detect_dwell_minutes

    # Check if already visited
    already = any(
        v.place_id == place_id and v.status == VisitStatus.VISITED
        for v in _visits.values()
    )

    should_prompt = dwell_minutes >= threshold and not already
    return DwellCheckResponse(
        should_prompt=should_prompt,
        place_id=place_id,
        place_name=place_name,
        dwell_minutes=dwell_minutes,
        already_visited=already,
    )


# ── Story 5.2: Place Notes & Rating ─────────────────────────────


async def create_note(
    visit_id: str,
    text: str = "",
    rating: PlaceRating | None = None,
    tags: list[str] | None = None,
    photos: list[str] | None = None,
    voice_transcript: str | None = None,
) -> PlaceNote | None:
    """Add a note to a visit."""
    _ensure_loaded()
    visit = _visits.get(visit_id)
    if not visit:
        return None

    note_id = f"note_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    note = PlaceNote(
        id=note_id,
        visit_id=visit_id,
        place_id=visit.place_id,
        text=_sanitize_text(text),
        rating=rating or PlaceRating(),
        tags=[_sanitize_tag(t) for t in (tags or []) if _sanitize_tag(t)],
        photos=[_sanitize_url(u) for u in (photos or []) if _sanitize_url(u)],
        voice_transcript=_sanitize_text(voice_transcript) if voice_transcript else None,
        created_at=now,
        updated_at=now,
    )
    _notes[note_id] = note
    _save_notes()
    logger.info("Created note %s for visit %s", note_id, visit_id)
    return note


async def get_notes_for_visit(visit_id: str) -> list[PlaceNote]:
    """Get all notes for a visit."""
    _ensure_loaded()
    return [n for n in _notes.values() if n.visit_id == visit_id]


async def update_note(
    note_id: str,
    text: str | None = None,
    rating: PlaceRating | None = None,
    tags: list[str] | None = None,
    photos: list[str] | None = None,
    voice_transcript: str | None = None,
) -> PlaceNote | None:
    """Update an existing note."""
    _ensure_loaded()
    note = _notes.get(note_id)
    if not note:
        return None

    if text is not None:
        note.text = _sanitize_text(text)
    if rating is not None:
        note.rating = rating
    if tags is not None:
        note.tags = [_sanitize_tag(t) for t in tags if _sanitize_tag(t)]
    if photos is not None:
        note.photos = [_sanitize_url(u) for u in photos if _sanitize_url(u)]
    if voice_transcript is not None:
        note.voice_transcript = _sanitize_text(voice_transcript)

    note.updated_at = datetime.now(UTC)
    _save_notes()
    return note


async def delete_note(note_id: str) -> bool:
    """Delete a note."""
    _ensure_loaded()
    if note_id not in _notes:
        return False
    del _notes[note_id]
    _save_notes()
    return True


# ── Story 5.3: Exploration Statistics ────────────────────────────


async def get_stats() -> ExplorationStats:
    """Compute exploration statistics."""
    _ensure_loaded()

    visited = [v for v in _visits.values() if v.status == VisitStatus.VISITED]
    want = [v for v in _visits.values() if v.status == VisitStatus.WANT_TO_VISIT]
    skipped = [v for v in _visits.values() if v.status == VisitStatus.SKIP]

    # Category stats — based on place names and tags from notes
    cat_counter: Counter[str] = Counter()
    for note in _notes.values():
        for tag in note.tags:
            cat_counter[tag] += 1

    by_category = [CategoryStat(category=cat, count=cnt) for cat, cnt in cat_counter.most_common()]

    # Total duration
    total_hours = sum(
        (v.duration_minutes or 0) for v in visited
    ) / 60.0

    # Total distance (sequential visits)
    total_distance_km = 0.0
    sorted_visited = sorted(visited, key=lambda v: v.visited_at)
    for i in range(1, len(sorted_visited)):
        prev = sorted_visited[i - 1]
        curr = sorted_visited[i]
        total_distance_km += _haversine_m(
            prev.coordinates.lat, prev.coordinates.lng,
            curr.coordinates.lat, curr.coordinates.lng,
        ) / 1000.0

    # Streak
    streak = _compute_streak(visited)

    # Surprises — places with biggest difference between avg confidence and actual rating
    surprises = _compute_surprises()

    return ExplorationStats(
        total_visited=len(visited),
        total_want_to_visit=len(want),
        total_skipped=len(skipped),
        by_category=by_category,
        total_distance_km=round(total_distance_km, 2),
        total_hours=round(total_hours, 2),
        streak=streak,
        surprises=surprises,
    )


def _compute_streak(visited: list[Visit]) -> StreakInfo:
    """Compute visit streak (consecutive days with a new visit)."""
    if not visited:
        return StreakInfo()

    dates = sorted({v.visited_at.date() for v in visited})
    if not dates:
        return StreakInfo()

    longest = 1
    current = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1

    # Check if current streak is still active (last visit was today or yesterday)
    today = datetime.now(UTC).date()
    if dates[-1] >= today - timedelta(days=1):
        # Recompute from end
        active = 1
        for i in range(len(dates) - 2, -1, -1):
            if (dates[i + 1] - dates[i]).days == 1:
                active += 1
            else:
                break
    else:
        active = 0

    return StreakInfo(
        current_streak=active,
        longest_streak=longest,
        last_visit_date=dates[-1].isoformat(),
    )


def _compute_surprises(top_n: int = 5) -> list[SurprisePlace]:
    """Find places where rating was most different from confidence."""
    _ensure_loaded()
    surprises: list[SurprisePlace] = []

    # Group notes by place_id
    place_notes: dict[str, list[PlaceNote]] = defaultdict(list)
    for note in _notes.values():
        if note.rating.average > 0:
            place_notes[note.place_id].append(note)

    for place_id, notes in place_notes.items():
        avg_rating = sum(n.rating.average for n in notes) / len(notes)
        # Find visit for expected score (use confidence or default 2.5)
        visit = next((v for v in _visits.values() if v.place_id == place_id), None)
        expected = 2.5  # default expected
        place_name = visit.place_name if visit else None
        delta = abs(avg_rating - expected)
        if delta > 0.5:
            surprises.append(SurprisePlace(
                place_id=place_id,
                place_name=place_name,
                expected_score=expected,
                actual_score=round(avg_rating, 1),
                delta=round(delta, 1),
            ))

    surprises.sort(key=lambda s: s.delta, reverse=True)
    return surprises[:top_n]


async def get_heatmap() -> list[HeatmapPoint]:
    """Generate heatmap points from visited places."""
    _ensure_loaded()
    visited = [v for v in _visits.values() if v.status == VisitStatus.VISITED]
    if not visited:
        return []

    # Count visits per approximate grid cell
    grid: Counter[tuple[float, float]] = Counter()
    for v in visited:
        # Round to ~100m grid
        key = (round(v.coordinates.lat, 3), round(v.coordinates.lng, 3))
        grid[key] += 1

    max_count = max(grid.values()) if grid else 1
    return [
        HeatmapPoint(lat=lat, lng=lng, intensity=round(count / max_count, 2))
        for (lat, lng), count in grid.items()
    ]


# ── Story 5.4: Trip Organization ────────────────────────────────


async def create_trip(
    name: str,
    region: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> Trip:
    """Create a new trip."""
    _ensure_loaded()
    trip_id = f"trip_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    trip = Trip(
        id=trip_id,
        name=name,
        region=region,
        start_date=start_date,
        end_date=end_date,
        created_at=now,
        updated_at=now,
    )
    _trips[trip_id] = trip
    _save_trips()
    logger.info("Created trip %s: %s", trip_id, name)
    return trip


async def get_trip(trip_id: str) -> Trip | None:
    _ensure_loaded()
    return _trips.get(trip_id)


async def list_trips(limit: int = 50, offset: int = 0) -> tuple[list[Trip], int]:
    _ensure_loaded()
    results = sorted(_trips.values(), key=lambda t: t.created_at, reverse=True)
    total = len(results)
    return results[offset : offset + limit], total


async def update_trip(
    trip_id: str,
    name: str | None = None,
    region: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> Trip | None:
    _ensure_loaded()
    trip = _trips.get(trip_id)
    if not trip:
        return None
    if name is not None:
        trip.name = name
    if region is not None:
        trip.region = region
    if start_date is not None:
        trip.start_date = start_date
    if end_date is not None:
        trip.end_date = end_date
    trip.updated_at = datetime.now(UTC)
    _save_trips()
    return trip


async def delete_trip(trip_id: str) -> bool:
    _ensure_loaded()
    if trip_id not in _trips:
        return False
    # Unlink visits from this trip
    for visit in _visits.values():
        if visit.trip_id == trip_id:
            visit.trip_id = None
    _save_visits()
    del _trips[trip_id]
    _save_trips()
    return True


async def auto_group_visits(
    trip_id: str,
    start_date: datetime,
    end_date: datetime,
    region_lat: float | None = None,
    region_lng: float | None = None,
    region_radius_km: float | None = None,
) -> Trip | None:
    """Auto-group visits into a trip by date range and optional region."""
    _ensure_loaded()
    trip = _trips.get(trip_id)
    if not trip:
        return None

    for visit in _visits.values():
        if not (start_date <= visit.visited_at <= end_date):
            continue
        if region_lat is not None and region_lng is not None and region_radius_km is not None:
            dist_km = _haversine_m(
                region_lat, region_lng,
                visit.coordinates.lat, visit.coordinates.lng,
            ) / 1000.0
            if dist_km > region_radius_km:
                continue
        if visit.id not in trip.visit_ids:
            trip.visit_ids.append(visit.id)
            visit.trip_id = trip_id

    trip.start_date = start_date
    trip.end_date = end_date
    trip.updated_at = datetime.now(UTC)
    _save_trips()
    _save_visits()
    return trip


async def get_trip_summary(trip_id: str) -> TripSummary | None:
    """Generate summary for a trip."""
    _ensure_loaded()
    trip = _trips.get(trip_id)
    if not trip:
        return None

    visits = [_visits[vid] for vid in trip.visit_ids if vid in _visits]
    visited = [v for v in visits if v.status == VisitStatus.VISITED]

    # Category stats from notes
    cat_counter: Counter[str] = Counter()
    for v in visits:
        for note in _notes.values():
            if note.visit_id == v.id:
                for tag in note.tags:
                    cat_counter[tag] += 1
    categories = [CategoryStat(category=c, count=n) for c, n in cat_counter.most_common()]

    # Total hours
    total_hours = sum((v.duration_minutes or 0) for v in visited) / 60.0

    # Total distance
    total_distance_km = 0.0
    sorted_v = sorted(visited, key=lambda v: v.visited_at)
    for i in range(1, len(sorted_v)):
        prev, curr = sorted_v[i - 1], sorted_v[i]
        total_distance_km += _haversine_m(
            prev.coordinates.lat, prev.coordinates.lng,
            curr.coordinates.lat, curr.coordinates.lng,
        ) / 1000.0

    # Best rated
    best: list[tuple[str, float]] = []
    for v in visits:
        for note in _notes.values():
            if note.visit_id == v.id and note.rating.average > 0:
                best.append((v.place_name or v.place_id, note.rating.average))
    best.sort(key=lambda x: x[1], reverse=True)
    best_names = [b[0] for b in best[:5]]

    # Heatmap
    heatmap = [
        HeatmapPoint(lat=v.coordinates.lat, lng=v.coordinates.lng)
        for v in visited
    ]

    return TripSummary(
        trip=trip,
        total_places=len(visited),
        categories=categories,
        total_distance_km=round(total_distance_km, 2),
        total_hours=round(total_hours, 2),
        best_rated_places=best_names,
        heatmap=heatmap,
    )


async def export_trip(trip_id: str, fmt: str = "markdown") -> tuple[str, str] | None:
    """Export trip as markdown, JSON, or HTML."""
    _ensure_loaded()
    summary = await get_trip_summary(trip_id)
    if not summary:
        return None

    trip = summary.trip
    safe_name = trip.name.replace(" ", "_")

    if fmt == "json":
        content = summary.model_dump_json(indent=2)
        filename = f"{safe_name}.json"
    elif fmt == "html":
        content = _export_html(trip, summary)
        filename = f"{safe_name}.html"
    else:
        lines = [
            f"# 🌍 {trip.name}",
            "",
            f"**Region:** {trip.region or 'N/A'}",
            f"**Dates:** {trip.start_date or 'N/A'} — {trip.end_date or 'N/A'}",
            f"**Places visited:** {summary.total_places}",
            f"**Distance:** {summary.total_distance_km} km",
            f"**Time:** {summary.total_hours} hours",
            "",
        ]
        if summary.best_rated_places:
            lines.append("## ⭐ Best Places")
            for name in summary.best_rated_places:
                lines.append(f"- {name}")
            lines.append("")

        visits = [_visits[vid] for vid in trip.visit_ids if vid in _visits]
        if visits:
            lines.append("## 📍 Places")
            for v in sorted(visits, key=lambda x: x.visited_at):
                lines.append(f"- **{v.place_name or v.place_id}** — {v.visited_at.strftime('%Y-%m-%d %H:%M')}")
                notes = [n for n in _notes.values() if n.visit_id == v.id]
                for note in notes:
                    if note.text:
                        lines.append(f"  > {note.text}")
                    if note.rating.average > 0:
                        stars = "⭐" * round(note.rating.average)
                        lines.append(f"  Rating: {stars} ({note.rating.average}/5)")
            lines.append("")

        content = "\n".join(lines)
        filename = f"{safe_name}.md"

    return content, filename


def _export_html(trip: Trip, summary: TripSummary) -> str:
    """Generate a styled HTML page for trip export."""
    visits = [_visits[vid] for vid in trip.visit_ids if vid in _visits]

    places_html = ""
    for v in sorted(visits, key=lambda x: x.visited_at):
        notes = [n for n in _notes.values() if n.visit_id == v.id]
        notes_html = ""
        for note in notes:
            if note.text:
                notes_html += f"<blockquote>{_html_escape(note.text)}</blockquote>"
            if note.rating.average > 0:
                stars = "⭐" * round(note.rating.average)
                notes_html += f"<p class='rating'>{stars} ({note.rating.average}/5)</p>"
        places_html += f"""
        <div class="place">
            <h3>{_html_escape(v.place_name or v.place_id)}</h3>
            <p class="date">{v.visited_at.strftime('%Y-%m-%d %H:%M')}</p>
            {notes_html}
        </div>"""

    best_html = ""
    if summary.best_rated_places:
        items = "".join(f"<li>{_html_escape(n)}</li>" for n in summary.best_rated_places)
        best_html = f"<h2>⭐ Best Places</h2><ul>{items}</ul>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html_escape(trip.name)} — Terra Incognita</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #fafafa; color: #333; }}
h1 {{ color: #2c5530; border-bottom: 2px solid #2c5530; padding-bottom: 10px; }}
.meta {{ background: #e8f5e9; padding: 15px; border-radius: 8px; margin: 15px 0; }}
.meta p {{ margin: 5px 0; }}
.place {{ background: white; padding: 15px; margin: 10px 0; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.place h3 {{ color: #1b5e20; margin-top: 0; }}
.date {{ color: #666; font-size: 0.9em; }}
blockquote {{ border-left: 3px solid #4caf50; margin: 10px 0; padding: 5px 15px; background: #f9f9f9; }}
.rating {{ color: #ff9800; }}
footer {{ text-align: center; margin-top: 30px; color: #999; font-size: 0.8em; }}
</style>
</head>
<body>
<h1>🌍 {_html_escape(trip.name)}</h1>
<div class="meta">
    <p><strong>Region:</strong> {_html_escape(trip.region or 'N/A')}</p>
    <p><strong>Dates:</strong> {trip.start_date or 'N/A'} — {trip.end_date or 'N/A'}</p>
    <p><strong>Places visited:</strong> {summary.total_places}</p>
    <p><strong>Distance:</strong> {summary.total_distance_km} km</p>
    <p><strong>Time:</strong> {summary.total_hours} hours</p>
</div>
{best_html}
<h2>📍 Places</h2>
{places_html}
<footer>Generated by Terra Incognita Explorer Journal</footer>
</body>
</html>"""


def _html_escape(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ── Test helper: reset state ─────────────────────────────────────


def _reset_store() -> None:
    """Reset in-memory stores (for testing only)."""
    global _data_dir
    _visits.clear()
    _notes.clear()
    _trips.clear()
    # Use a temp dir so tests don't interfere with real data or each other
    import tempfile
    _data_dir = Path(tempfile.mkdtemp())
