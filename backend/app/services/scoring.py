"""Unusualness scoring: "how likely is this a genuinely unusual place worth a detour?".

Rule-based, deterministic and pure (no I/O, no clock, no randomness), so the same place always
gets the same score and every number can be explained. The model is a noisy-OR over evidence:

    p = 1 - (1 - base) * Π(1 - signalᵢ)         base = rarity of the best category
    p = p * Π(factorⱼ)                           anti-signals: mainstream, ticketed, unnamed ...

* ``base``     — category rarity (abandoned / underground / cave / ruins / military high;
                 museum / restaurant / park / landmark low).
* signals      — Atlas Obscura listing, OSM lifecycle tags (abandoned / disused / ruins),
                 bunker / mine / cave tags, historic age, restricted access, being off the
                 tourist path (no Wikipedia article), and a small data-richness bonus so an
                 unknown blob does not outrank a documented bunker.
* anti-signals — famous (many Wikipedia language editions), has a Wikipedia article,
                 ``tourism=attraction``, ticketed/commercial, ordinary hilltops, unnamed.

Only the *positive* signals become ``unusual_reasons`` (short English phrases); anti-signals are
available on :class:`ScoreResult.penalties` for debugging and tests.

``diversify_by_site`` is a separate, optional re-ranking step so a ranked list does not open
with five pieces of the same ruin complex.
"""

from __future__ import annotations

import heapq
import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from app.models.place import Place, PlaceCategory, PlaceSource
from app.services import names
from app.sources.osm_tags import OsmInterpretation, describe_date, interpret_tags, parse_year

C = PlaceCategory

# ── Category rarity ────────────────────────────────────────────

CATEGORY_WEIGHTS: dict[PlaceCategory, float] = {
    C.UNDERGROUND: 0.65,
    C.CAVE: 0.65,
    C.ABANDONED: 0.60,
    C.RUINS: 0.60,
    C.MILITARY: 0.55,
    C.UNDERWATER: 0.55,
    C.INDUSTRIAL: 0.42,
    C.STREET_ART: 0.35,
    C.NATURE_HIDDEN: 0.32,
    C.TRANSPORT: 0.28,
    C.WATER: 0.25,
    C.VIEWPOINT: 0.20,
    C.ARCHITECTURE: 0.16,
    C.LANDMARK: 0.15,
    C.MONUMENT: 0.12,
    C.CULINARY: 0.12,
    C.RELIGIOUS: 0.10,
    C.RESTAURANT_NOTABLE: 0.10,
    C.MUSEUM: 0.08,
    C.PARK: 0.08,
    C.HOTEL_NOTABLE: 0.06,
}
DEFAULT_BASE = 0.12  # nothing is known about what it is
_MAX_NAMESAKE_TOKEN_FREQUENCY = 60  # a name word used by more places than this is an area word
_REASON_MIN_WEIGHT = 0.40  # categories rarer than this are explained to the user

CATEGORY_REASONS: dict[PlaceCategory, str] = {
    C.UNDERGROUND: "underground",
    C.CAVE: "cave",
    C.ABANDONED: "abandoned",
    C.RUINS: "ruins",
    C.MILITARY: "military site",
    C.UNDERWATER: "underwater site",
    C.INDUSTRIAL: "industrial heritage",
    C.STREET_ART: "street art",
    C.NATURE_HIDDEN: "hidden nature",
    C.TRANSPORT: "forgotten transport",
}
_SECOND_CATEGORY_SHARE = 0.25  # a second rare category adds this share of its weight

# ── Context handed to every rule ───────────────────────────────

_RESTRICTED_ACCESS = frozenset({"private", "no", "military", "permit", "restricted"})
_BORING_TUNNELS = frozenset({"no", "culvert", "building_passage", "flooded", "passage"})
_BUNKER_FEATURES = frozenset({"military=bunker", "building=bunker", "historic=bunker"})
_MINE_FEATURES = frozenset({"man_made=adit", "man_made=mineshaft", "historic=mine", "historic=mine_shaft"})
_CAVE_FEATURES = frozenset({"natural=cave_entrance", "natural=sinkhole"})
_INDUSTRY_FEATURES = frozenset({
    "man_made=water_tower", "man_made=silo", "man_made=chimney", "man_made=windmill",
    "man_made=watermill", "man_made=kiln", "historic=lime_kiln", "historic=kiln",
    "historic=charcoal_pile",
})
# Military sites that are still in use: not a hidden gem, and often not a safe or legal visit.
_ACTIVE_MILITARY = frozenset({
    "base", "barracks", "airfield", "naval_base", "training_area", "checkpoint", "ammunition",
    "launchpad", "nuclear_explosion_site", "range", "danger_area", "office",
})
_WIKI_META_KEYS = ("wikipedia_url", "wikipedia_title", "wikipedia")


@dataclass(frozen=True)
class _Ctx:
    place: Place
    tags: dict[str, str]
    info: OsmInterpretation | None
    cats: frozenset[PlaceCategory]
    sources: frozenset[PlaceSource]
    base: float
    named: bool
    sitelinks: int | None
    has_wikipedia: bool
    year: int | None
    date_text: str = ""  # the raw start_date-like value, for wording the age honestly

    @property
    def feature(self) -> str | None:
        return self.info.feature if self.info else None

    def has_feature(self, wanted: frozenset[str]) -> bool:
        """Does the primary feature, or any plain ``key=value`` tag, match one of ``wanted``?"""
        if self.feature in wanted:
            return True
        return any(f"{k}={v}" in wanted for k, v in self.tags.items())


@dataclass(frozen=True)
class Signal:
    """Evidence that raises the score by ``weight`` (noisy-OR). ``reason=None`` = silent.

    ``priority`` orders the reasons shown to the user (lower = earlier); category reasons use
    :data:`CATEGORY_REASON_PRIORITY`.
    """

    reason: str | Callable[[_Ctx], str] | None
    weight: float
    test: Callable[[_Ctx], bool]
    priority: int = 100


@dataclass(frozen=True)
class Penalty:
    """Evidence of mainstream-ness: multiplies the score by ``factor`` (< 1)."""

    label: str | Callable[[_Ctx], str]
    factor: float
    test: Callable[[_Ctx], bool]


def _year_reason(ctx: _Ctx) -> str:
    return describe_date(ctx.date_text) or f"dates from {ctx.year}"


def _sitelinks_label(ctx: _Ctx) -> str:
    return f"documented in {ctx.sitelinks} Wikipedia language editions"


def _is_tunnel(c: _Ctx) -> bool:
    if c.feature == "man_made=tunnel":
        return True
    tunnel = c.tags.get("tunnel")
    return bool(tunnel) and tunnel not in _BORING_TUNNELS and C.UNDERGROUND in c.cats


CATEGORY_REASON_PRIORITY = 40

SIGNALS: tuple[Signal, ...] = (
    # sources
    Signal("listed on Atlas Obscura", 0.35,
           lambda c: PlaceSource.ATLAS_OBSCURA in c.sources, priority=10),
    Signal("added by the community", 0.12,
           lambda c: PlaceSource.COMMUNITY in c.sources, priority=12),
    Signal("surfaced by deep research", 0.10,
           lambda c: PlaceSource.DEEP_RESEARCH in c.sources, priority=80),
    Signal("confirmed by several sources", 0.04,
           lambda c: len(c.sources - {PlaceSource.LLM}) >= 2, priority=85),
    # what it is
    Signal("military bunker", 0.20, lambda c: c.has_feature(_BUNKER_FEATURES), priority=20),
    Signal("old mine workings", 0.20, lambda c: c.has_feature(_MINE_FEATURES), priority=21),
    Signal("cave entrance", 0.15, lambda c: c.has_feature(_CAVE_FEATURES), priority=22),
    Signal("shipwreck", 0.15, lambda c: c.has_feature(frozenset({"historic=wreck"})), priority=23),
    Signal("tunnel", 0.12, _is_tunnel, priority=24),
    Signal("industrial heritage", 0.08, lambda c: c.has_feature(_INDUSTRY_FEATURES), priority=50),
    Signal(None, 0.04, lambda c: "bunker_type" in c.tags),
    # OSM lifecycle
    Signal("abandoned", 0.25, lambda c: bool(c.info and "abandoned" in c.info.lifecycles),
           priority=30),
    Signal("disused", 0.20, lambda c: bool(c.info and "disused" in c.info.lifecycles),
           priority=31),
    Signal("ruins", 0.22, lambda c: bool(c.info and "ruins" in c.info.lifecycles), priority=32),
    # age
    Signal(_year_reason, 0.10, lambda c: c.year is not None and c.year <= 1800, priority=60),
    Signal(_year_reason, 0.07, lambda c: c.year is not None and 1800 < c.year <= 1900, priority=60),
    Signal(_year_reason, 0.04, lambda c: c.year is not None and 1900 < c.year <= 1950, priority=60),
    Signal("ancient site", 0.06, lambda c: bool(c.place.metadata.get("historic_period")),
           priority=61),
    # access
    Signal("restricted access", 0.05,
           lambda c: str(c.place.metadata.get("access") or c.tags.get("access", "")).lower()
           in _RESTRICTED_ACCESS, priority=70),
    # off the beaten track — only worth saying about places that are interesting at all
    Signal("no Wikipedia article — off the tourist path", 0.06,
           lambda c: c.base >= 0.40 and not c.has_wikipedia and not (c.sitelinks or 0),
           priority=90),
    # data richness (silent): documented places beat unknown blobs
    Signal(None, 0.03, lambda c: c.named),
    Signal(None, 0.04, lambda c: len((c.place.description or "").strip()) >= 30),
    Signal(None, 0.03, lambda c: bool(c.place.photos)),
    Signal(None, 0.02, lambda c: len(c.place.tags) >= 4),
)

# A specific reason makes the general ones it implies redundant.
_SUPERSEDES: dict[str, frozenset[str]] = {
    "military bunker": frozenset({"military site", "underground"}),
    "old mine workings": frozenset({"underground", "industrial heritage"}),
    "cave entrance": frozenset({"cave", "underground"}),
    "tunnel": frozenset({"underground"}),
    "disused": frozenset({"abandoned"}),
    "shipwreck": frozenset({"underwater site"}),
}

PENALTIES: tuple[Penalty, ...] = (
    Penalty(_sitelinks_label, 0.45, lambda c: (c.sitelinks or 0) >= 40),
    Penalty(_sitelinks_label, 0.60, lambda c: 20 <= (c.sitelinks or 0) < 40),
    Penalty(_sitelinks_label, 0.78, lambda c: 10 <= (c.sitelinks or 0) < 20),
    Penalty(_sitelinks_label, 0.90, lambda c: 5 <= (c.sitelinks or 0) < 10),
    Penalty("has a Wikipedia article", 0.92,
            lambda c: c.has_wikipedia and (c.sitelinks or 0) < 5),
    Penalty("active military site", 0.60,
            lambda c: c.tags.get("military") in _ACTIVE_MILITARY
            and not (c.info and c.info.lifecycles)),
    Penalty("marked as a tourist attraction", 0.80, lambda c: c.tags.get("tourism") == "attraction"),
    # (Atlas Obscura's museums are the quirky ones; its curation already says "unusual".)
    Penalty("museum (a managed visitor attraction)", 0.55,
            lambda c: (c.tags.get("tourism") == "museum" or C.MUSEUM in c.cats)
            and PlaceSource.ATLAS_OBSCURA not in c.sources),
    Penalty("ticketed / commercial", 0.85,
            lambda c: c.tags.get("fee") == "yes" or bool(c.tags.get("opening_hours"))
            or bool(c.place.metadata.get("opening_hours"))),
    Penalty("ordinary hilltop", 0.70, lambda c: c.tags.get("natural") == "peak"),
    Penalty("ordinary viewpoint", 0.85, lambda c: c.tags.get("tourism") == "viewpoint"),
    Penalty("nature reserve", 0.85, lambda c: c.tags.get("leisure") == "nature_reserve"),
    Penalty("unnamed", 0.85, lambda c: not c.named),
)


# ── Scoring ────────────────────────────────────────────────────


@dataclass
class ScoreResult:
    score: float
    reasons: list[str]
    penalties: list[str] = field(default_factory=list)
    base: float = 0.0


def _context(place: Place) -> _Ctx:
    tags = place.metadata.get("osm_tags")
    tags = {str(k): str(v) for k, v in tags.items()} if isinstance(tags, dict) else {}
    info = interpret_tags(tags) if tags else None

    cats = frozenset(place.categories)
    base = max((CATEGORY_WEIGHTS.get(c, DEFAULT_BASE) for c in cats), default=DEFAULT_BASE)

    sources = {place.source, *place.sources}
    named = bool(place.name and place.name.strip()) and not place.metadata.get("name_generated")

    raw_links = place.metadata.get("sitelinks")
    sitelinks = int(raw_links) if isinstance(raw_links, int | float) and raw_links >= 0 else None

    has_wikipedia = bool(
        place.wikipedia
        or tags.get("wikipedia")
        or any(place.metadata.get(k) for k in _WIKI_META_KEYS)
    )
    date_text = str(place.metadata.get("start_date") or tags.get("start_date") or "")
    year = parse_year(date_text or None)
    return _Ctx(place, tags, info, cats, frozenset(sources), base, named, sitelinks,
                has_wikipedia, year, date_text)


def _second_best(cats: frozenset[PlaceCategory]) -> float:
    weights = sorted((CATEGORY_WEIGHTS.get(c, DEFAULT_BASE) for c in cats), reverse=True)
    return weights[1] if len(weights) > 1 else 0.0


def score_place(place: Place) -> ScoreResult:
    """Score one place. Pure: depends only on the place's own fields."""
    ctx = _context(place)
    remaining = 1.0 - ctx.base
    explained: list[tuple[int, float, str]] = []  # (priority, -weight, reason)

    # a second rare category (abandoned *and* military ...) is corroboration
    second = _second_best(ctx.cats)
    if second >= _REASON_MIN_WEIGHT:
        remaining *= 1.0 - _SECOND_CATEGORY_SHARE * second

    for cat in ctx.cats:
        weight = CATEGORY_WEIGHTS.get(cat, DEFAULT_BASE)
        if weight >= _REASON_MIN_WEIGHT and cat in CATEGORY_REASONS:
            explained.append((CATEGORY_REASON_PRIORITY, -weight, CATEGORY_REASONS[cat]))

    for sig in SIGNALS:
        if not sig.test(ctx):
            continue
        remaining *= 1.0 - sig.weight
        if sig.reason is not None:
            text = sig.reason(ctx) if callable(sig.reason) else sig.reason
            explained.append((sig.priority, -sig.weight, text))

    score = 1.0 - remaining
    penalties: list[str] = []
    for pen in PENALTIES:
        if pen.test(ctx):
            score *= pen.factor
            penalties.append(pen.label(ctx) if callable(pen.label) else pen.label)

    reasons: list[str] = []
    for _, _, text in sorted(explained):
        if text not in reasons:
            reasons.append(text)
    for specific, general in _SUPERSEDES.items():
        if specific in reasons:
            reasons = [r for r in reasons if r not in general]
    return ScoreResult(
        score=round(min(max(score, 0.0), 1.0), 3),
        reasons=reasons[:5],
        penalties=penalties,
        base=ctx.base,
    )


def apply_unusual_scores(places: list[Place]) -> list[Place]:
    """Set ``unusual_score`` / ``unusual_reasons`` on every place (in place) and return the list."""
    for place in places:
        result = score_place(place)
        place.unusual_score = result.score
        place.unusual_reasons = result.reasons
    return places


def unusual_sort_key(place: Place) -> tuple[float, float, str, str]:
    """Most unusual first; ties broken by confidence, then name, then id (fully deterministic)."""
    return (-place.unusual_score, -place.confidence, (place.name or "").lower(), place.id)


# ── Site diversity ─────────────────────────────────────────────


def diversify_by_site(
    ranked: list[Place],
    *,
    site_radius_m: float = 150.0,
    chain_radius_m: float = 20_000.0,
    decay: float = 0.6,
    floor: float = 0.3,
    name_decay: float = 0.8,
    name_floor: float = 0.5,
) -> list[Place]:
    """Re-rank so that parts of one site do not crowd the top of the list.

    ``ranked`` must already be sorted best-first. A place's effective score is its
    ``unusual_score`` times a factor for each kind of higher-ranked "sibling":

    * *neighbours* within ``site_radius_m`` (the eleven ruined buildings of one fort, a chain of
      pillboxes): ``max(decay ** k, floor)``;
    * *namesakes* within ``chain_radius_m`` sharing a distinctive name (twelve segments of one
      abandoned railway, several pens of one submarine base): the milder
      ``max(name_decay ** k, name_floor)``.

    Nothing is dropped: siblings sink (never below the floors, so a ruined neighbour still
    outranks a museum), and unrelated places keep their relative order. Deterministic (ties
    fall back to the incoming order).
    """
    n = len(ranked)
    if n < 2:
        return list(ranked)

    lat0 = math.radians(sum(p.coordinates.lat for p in ranked) / n)
    m_per_deg = 111_320.0
    xs = [p.coordinates.lng * m_per_deg * max(math.cos(lat0), 0.01) for p in ranked]
    ys = [p.coordinates.lat * m_per_deg for p in ranked]
    cell = max(site_radius_m, 1.0)

    def cell_of(i: int) -> tuple[int, int]:
        return (int(xs[i] // cell), int(ys[i] // cell))

    # Words shared by dozens of names are area words ("Kotor", a street), not identities: they
    # would make every place in town a namesake of every other (and the search quadratic).
    all_tokens = [
        names.distinctive_tokens(p.name) if not p.metadata.get("name_generated") else frozenset()
        for p in ranked
    ]
    document_frequency = Counter(token for tokens in all_tokens for token in tokens)
    name_tokens = [
        frozenset(t for t in tokens if document_frequency[t] <= _MAX_NAMESAKE_TOKEN_FREQUENCY)
        for tokens in all_tokens
    ]
    grid: dict[tuple[int, int], list[int]] = {}
    by_token: dict[str, list[int]] = {}  # distinctive name token → selected places carrying it

    def close(i: int, j: int, limit: float) -> bool:
        return math.hypot(xs[i] - xs[j], ys[i] - ys[j]) <= limit

    def crowding(i: int) -> tuple[int, int]:
        """(selected neighbours within the site radius, selected namesakes elsewhere in the region)"""
        cx, cy = cell_of(i)
        near: set[int] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                near.update(j for j in grid.get((cx + dx, cy + dy), ()) if close(i, j, site_radius_m))
        siblings = {j for token in name_tokens[i] for j in by_token.get(token, ())} - near
        namesakes = {
            j for j in siblings
            if close(i, j, chain_radius_m)
            and names.token_similarity(name_tokens[i], name_tokens[j]) >= names.STRONG_MATCH
        }
        return len(near), len(namesakes)

    def effective(i: int, crowd: tuple[int, int]) -> float:
        return (
            ranked[i].unusual_score
            * max(decay ** crowd[0], floor)
            * max(name_decay ** crowd[1], name_floor)
        )

    # Lazy greedy selection: a candidate's effective score only ever drops as siblings are
    # selected, so a stale heap entry is re-checked on pop and pushed back if it got worse.
    heap = [(-p.unusual_score, i, (0, 0)) for i, p in enumerate(ranked)]
    heapq.heapify(heap)
    out: list[Place] = []
    while heap:
        _, i, seen = heapq.heappop(heap)
        crowd = crowding(i)
        if crowd != seen:
            heapq.heappush(heap, (-effective(i, crowd), i, crowd))
            continue
        out.append(ranked[i])
        grid.setdefault(cell_of(i), []).append(i)
        for token in name_tokens[i]:
            by_token.setdefault(token, []).append(i)
    return out
