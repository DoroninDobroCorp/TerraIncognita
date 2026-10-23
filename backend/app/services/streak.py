"""Day streak: consecutive local calendar days with at least one journal visit.

A pure core (``compute_streak``) plus a thin reader over the journal's public API.

Rules:
- Days are *local* to the viewer: ``local = utc + tz_offset_minutes`` (east of UTC positive,
  i.e. ``-new Date().getTimezoneOffset()`` in JavaScript).
- A streak is alive if the last active day is today or yesterday, so the counter does not
  drop to zero before the explorer has had a chance to go out today.
- Timestamps later than the end of the viewer's today (clock skew) are ignored.
- Several visits on one day count once.

Only VISITED journal visits count. Notes are deliberately left out: the journal's public API
only offers per-visit note lookup (O(visits x notes)), which is not cheap enough here.
``compute_streak`` takes plain timestamps, so adding another activity source later is a
one-line change in ``get_streak``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from app.models.journal import VisitStatus
from app.services import journal

STREAK_WINDOW_DAYS = 60
MAX_TZ_OFFSET_MINUTES = 14 * 60
_ALL_VISITS = 10**9


@dataclass(frozen=True)
class StreakResult:
    current: int = 0
    longest: int = 0
    last_active: date | None = None
    active_today: bool = False
    days: list[date] = field(default_factory=list)


def local_day(ts: datetime, tz_offset_minutes: int = 0) -> date:
    """Calendar day of ``ts`` for a viewer ``tz_offset_minutes`` east of UTC (naive = UTC)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (ts.astimezone(UTC) + timedelta(minutes=tz_offset_minutes)).date()


def compute_streak(
    timestamps: Iterable[datetime], now: datetime, tz_offset_minutes: int = 0
) -> StreakResult:
    today = local_day(now, tz_offset_minutes)

    active: set[date] = set()
    for ts in timestamps:
        try:
            day = local_day(ts, tz_offset_minutes)
        except (OverflowError, ValueError):  # absurd timestamps near datetime.min/max
            continue
        if day <= today:
            active.add(day)
    if not active:
        return StreakResult()

    ordered = sorted(active)
    longest = run = 1
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)
    last = ordered[-1]  # ``run`` is now the length of the run that ends on ``last``

    window_start = today - timedelta(days=STREAK_WINDOW_DAYS - 1)
    return StreakResult(
        current=run if (today - last).days <= 1 else 0,
        longest=longest,
        last_active=last,
        active_today=last == today,
        days=[d for d in ordered if d >= window_start],
    )


async def get_streak(tz_offset_minutes: int = 0, now: datetime | None = None) -> StreakResult:
    visits, _ = await journal.list_visits(status=VisitStatus.VISITED, limit=_ALL_VISITS)
    return compute_streak(
        (v.visited_at for v in visits), now or datetime.now(UTC), tz_offset_minutes
    )
