"""Explorer Journal API endpoints (Epic 5)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.journal import (
    CheckInRequest,
    DwellCheckRequest,
    DwellCheckResponse,
    HeatmapResponse,
    NoteCreateRequest,
    NoteListResponse,
    NoteUpdateRequest,
    ProximityCheckRequest,
    ProximityCheckResponse,
    StatsResponse,
    TripAutoGroupRequest,
    TripCreateRequest,
    TripExportRequest,
    TripExportResponse,
    TripListResponse,
    TripSummaryResponse,
    TripUpdateRequest,
    Visit,
    VisitListResponse,
    VisitStatus,
    VisitUpdateRequest,
)
from app.services import journal as journal_service

router = APIRouter(prefix="/api", tags=["journal"])


# ── Story 5.1: Visit Tracking ───────────────────────────────────

# Fixed-path routes MUST come before /{visit_id} to avoid path parameter capture


@router.post("/visits/proximity", response_model=ProximityCheckResponse)
async def check_proximity(req: ProximityCheckRequest) -> ProximityCheckResponse:
    """Check if user is near a previously visited place."""
    return await journal_service.check_proximity(
        lat=req.lat, lng=req.lng, radius_m=req.radius_m
    )


@router.get("/visits/place-ids")
async def get_visited_place_ids() -> dict:
    """Get list of visited place IDs (for exclude_visited)."""
    ids = await journal_service.get_visited_place_ids()
    return {"place_ids": ids, "total": len(ids)}


@router.post("/visits/dwell-check", response_model=DwellCheckResponse)
async def dwell_check(req: DwellCheckRequest) -> DwellCheckResponse:
    """Check if user dwelled long enough to suggest check-in (>5 min)."""
    return await journal_service.check_dwell(
        place_id=req.place_id,
        lat=req.lat,
        lng=req.lng,
        dwell_minutes=req.dwell_minutes,
        place_name=req.place_name,
    )


@router.post("/visits", response_model=Visit)
async def create_visit(req: CheckInRequest) -> Visit:
    """Check in at a place (manual or auto-detected)."""
    return await journal_service.create_visit(
        place_id=req.place_id,
        lat=req.lat,
        lng=req.lng,
        place_name=req.place_name,
        status=req.status,
        duration_minutes=req.duration_minutes,
        trip_id=req.trip_id,
    )


@router.get("/visits", response_model=VisitListResponse)
async def list_visits(
    status: VisitStatus | None = None,
    trip_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> VisitListResponse:
    """List visits with optional filters."""
    visits, total = await journal_service.list_visits(
        status=status, trip_id=trip_id, limit=limit, offset=offset
    )
    return VisitListResponse(visits=visits, total=total)


@router.get("/visits/{visit_id}", response_model=Visit)
async def get_visit(visit_id: str) -> Visit:
    """Get a specific visit."""
    visit = await journal_service.get_visit(visit_id)
    if not visit:
        raise HTTPException(status_code=404, detail="Visit not found")
    return visit


@router.patch("/visits/{visit_id}", response_model=Visit)
async def update_visit(visit_id: str, req: VisitUpdateRequest) -> Visit:
    """Update visit status or details."""
    visit = await journal_service.update_visit(
        visit_id=visit_id,
        status=req.status,
        duration_minutes=req.duration_minutes,
        trip_id=req.trip_id,
    )
    if not visit:
        raise HTTPException(status_code=404, detail="Visit not found")
    return visit


@router.delete("/visits/{visit_id}")
async def delete_visit(visit_id: str) -> dict:
    """Delete a visit."""
    deleted = await journal_service.delete_visit(visit_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Visit not found")
    return {"deleted": True}


# ── Story 5.2: Place Notes & Rating ─────────────────────────────


@router.post("/visits/{visit_id}/notes")
async def create_note(visit_id: str, req: NoteCreateRequest):
    """Add a note to a visit."""
    note = await journal_service.create_note(
        visit_id=visit_id,
        text=req.text,
        rating=req.rating,
        tags=req.tags,
        photos=req.photos,
        voice_transcript=req.voice_transcript,
    )
    if not note:
        raise HTTPException(status_code=404, detail="Visit not found")
    return note


@router.get("/visits/{visit_id}/notes", response_model=NoteListResponse)
async def get_notes(visit_id: str) -> NoteListResponse:
    """Get all notes for a visit."""
    notes = await journal_service.get_notes_for_visit(visit_id)
    return NoteListResponse(notes=notes, total=len(notes))


@router.patch("/notes/{note_id}")
async def update_note(note_id: str, req: NoteUpdateRequest):
    """Update a note."""
    note = await journal_service.update_note(
        note_id=note_id,
        text=req.text,
        rating=req.rating,
        tags=req.tags,
        photos=req.photos,
        voice_transcript=req.voice_transcript,
    )
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str) -> dict:
    """Delete a note."""
    deleted = await journal_service.delete_note(note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": True}


# ── Story 5.3: Exploration Statistics ────────────────────────────


@router.get("/stats", response_model=StatsResponse)
async def get_stats() -> StatsResponse:
    """Get exploration statistics."""
    stats = await journal_service.get_stats()
    return StatsResponse(stats=stats)


@router.get("/stats/heatmap", response_model=HeatmapResponse)
async def get_heatmap() -> HeatmapResponse:
    """Get heatmap data from visited places."""
    points = await journal_service.get_heatmap()
    return HeatmapResponse(points=points, total=len(points))


# ── Story 5.4: Trip Organization ────────────────────────────────


@router.post("/trips")
async def create_trip(req: TripCreateRequest):
    """Create a new trip."""
    return await journal_service.create_trip(
        name=req.name,
        region=req.region,
        start_date=req.start_date,
        end_date=req.end_date,
    )


@router.get("/trips", response_model=TripListResponse)
async def list_trips(limit: int = 50, offset: int = 0) -> TripListResponse:
    """List all trips."""
    trips, total = await journal_service.list_trips(limit=limit, offset=offset)
    return TripListResponse(trips=trips, total=total)


@router.get("/trips/{trip_id}")
async def get_trip(trip_id: str):
    """Get a specific trip."""
    trip = await journal_service.get_trip(trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


@router.patch("/trips/{trip_id}")
async def update_trip(trip_id: str, req: TripUpdateRequest):
    """Update a trip."""
    trip = await journal_service.update_trip(
        trip_id=trip_id,
        name=req.name,
        region=req.region,
        start_date=req.start_date,
        end_date=req.end_date,
    )
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


@router.delete("/trips/{trip_id}")
async def delete_trip(trip_id: str) -> dict:
    """Delete a trip."""
    deleted = await journal_service.delete_trip(trip_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Trip not found")
    return {"deleted": True}


@router.get("/trips/{trip_id}/summary", response_model=TripSummaryResponse)
async def get_trip_summary(trip_id: str) -> TripSummaryResponse:
    """Get trip summary with statistics."""
    summary = await journal_service.get_trip_summary(trip_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Trip not found")
    return TripSummaryResponse(summary=summary)


@router.post("/trips/{trip_id}/auto-group")
async def auto_group_visits(trip_id: str, req: TripAutoGroupRequest):
    """Auto-group visits into a trip by date range and region."""
    trip = await journal_service.auto_group_visits(
        trip_id=trip_id,
        start_date=req.start_date,
        end_date=req.end_date,
        region_lat=req.region_lat,
        region_lng=req.region_lng,
        region_radius_km=req.region_radius_km,
    )
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


@router.post("/trips/{trip_id}/export", response_model=TripExportResponse)
async def export_trip(trip_id: str, req: TripExportRequest) -> TripExportResponse:
    """Export trip as markdown or JSON."""
    result = await journal_service.export_trip(trip_id, fmt=req.format)
    if not result:
        raise HTTPException(status_code=404, detail="Trip not found")
    content, filename = result
    return TripExportResponse(content=content, format=req.format, filename=filename)
