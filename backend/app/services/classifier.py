"""Story 1.5 — Category Classification.

Rule-based primary classification (fast, deterministic) with
LLM-based fallback for ambiguous or untagged places.
"""

from __future__ import annotations

import json
import logging

from app.models.place import Place, PlaceCategory

logger = logging.getLogger(__name__)

# ── Rule-based keyword → category mapping ──────────────────────────

_KEYWORD_RULES: list[tuple[list[str], list[PlaceCategory]]] = [
    # Unusual
    (["abandon", "derelict", "disused", "vacant", "deserted"],
     [PlaceCategory.ABANDONED]),
    (["ruin", "ruined", "collapsed"],
     [PlaceCategory.RUINS]),
    (["tunnel", "underground", "subway", "metro", "catacombs", "subterranean"],
     [PlaceCategory.UNDERGROUND]),
    (["cave", "grotto", "cavern", "karst"],
     [PlaceCategory.CAVE, PlaceCategory.UNDERGROUND]),
    (["bunker", "pillbox", "shelter", "military", "barracks", "fortification"],
     [PlaceCategory.MILITARY]),
    (["factory", "industrial", "warehouse", "silo", "mill", "plant", "chimney"],
     [PlaceCategory.INDUSTRIAL]),
    (["graffiti", "street art", "mural", "stencil"],
     [PlaceCategory.STREET_ART]),
    (["viewpoint", "lookout", "panorama", "observation"],
     [PlaceCategory.VIEWPOINT]),
    (["rail", "train", "locomotive", "station", "depot", "tram"],
     [PlaceCategory.TRANSPORT]),
    (["waterfall", "spring", "dam", "aqueduct", "reservoir"],
     [PlaceCategory.WATER]),
    (["hidden", "secret", "secluded", "off-trail"],
     [PlaceCategory.NATURE_HIDDEN]),

    # Classic
    (["castle", "palace", "fortress", "citadel"],
     [PlaceCategory.LANDMARK, PlaceCategory.ARCHITECTURE]),
    (["church", "cathedral", "chapel", "mosque", "synagogue", "temple", "monastery"],
     [PlaceCategory.RELIGIOUS]),
    (["museum", "gallery", "exhibition"],
     [PlaceCategory.MUSEUM]),
    (["monument", "memorial", "statue", "obelisk"],
     [PlaceCategory.MONUMENT]),
    (["park", "garden", "botanical"],
     [PlaceCategory.PARK]),
    (["bridge", "lighthouse", "tower", "gate", "arch"],
     [PlaceCategory.ARCHITECTURE]),

    # Notable restaurants & hotels (keywords indicating exceptional places)
    (["michelin", "starred", "historic restaurant", "legendary restaurant",
      "historic hotel", "heritage hotel", "palace hotel", "boutique hotel"],
     [PlaceCategory.RESTAURANT_NOTABLE, PlaceCategory.HOTEL_NOTABLE]),

    # Deep research categories
    (["shipwreck", "wreck", "diving", "snorkeling", "amphora", "underwater",
      "submerged", "seabed"],
     [PlaceCategory.UNDERWATER]),
    (["restaurant", "cuisine", "dish", "wine", "beer", "tasting", "culinary",
      "food", "tavern", "konoba"],
     [PlaceCategory.CULINARY]),
]

_LLM_CLASSIFY_PROMPT = """\
Classify this place into one or more categories. Respond ONLY with valid JSON.

Place info:
- Name: {name}
- Tags: {tags}
- Metadata: {metadata}
- Coordinates: {lat:.4f}, {lng:.4f}

Available categories: abandoned, underground, industrial, ruins, military, \
nature_hidden, viewpoint, street_art, transport, water, cave, \
religious, landmark, museum, architecture, monument, park, \
restaurant_notable, hotel_notable

Return JSON: {{"categories": ["cat1", "cat2"], "confidence": 0.7}}
Rules:
- Pick 1-3 most fitting categories
- confidence: 0.6-0.9 based on how certain you are
- restaurant_notable / hotel_notable: ONLY for truly exceptional places — \
  historic, Michelin-starred, architecturally unique, or culturally significant. \
  Never for ordinary restaurants or hotels.
- If truly unclear, return {{"categories": ["landmark"], "confidence": 0.3}}
"""


def classify_place(place: Place) -> Place:
    """Apply rule-based classification. Adds categories; never removes existing ones."""
    if place.categories and place.category_confidence:
        return place

    text = _searchable_text(place)
    new_cats: set[PlaceCategory] = set(place.categories)
    cat_confidence: dict[str, float] = dict(place.category_confidence)

    for keywords, cats in _KEYWORD_RULES:
        matches = sum(1 for kw in keywords if kw in text)
        if matches > 0:
            conf = min(0.5 + matches * 0.15, 1.0)
            for c in cats:
                new_cats.add(c)
                cat_confidence[c.value] = max(cat_confidence.get(c.value, 0), conf)

    if new_cats:
        place.categories = sorted(new_cats, key=lambda c: c.value)
        place.category_confidence = cat_confidence
    else:
        # Mark for LLM classification (low confidence default)
        place.categories = [PlaceCategory.LANDMARK]
        place.category_confidence = {PlaceCategory.LANDMARK.value: 0.3}
        place.metadata["_needs_llm_classification"] = True

    return place


async def classify_place_llm(place: Place) -> Place:
    """LLM-based classification fallback for places the rule engine couldn't classify."""
    from app.services.llm_client import chat_completion

    text = _searchable_text(place)
    tags_str = ", ".join(place.tags[:15]) if place.tags else "none"
    meta_str = ""
    if "osm_tags" in place.metadata:
        meta_str = ", ".join(f"{k}={v}" for k, v in list(place.metadata["osm_tags"].items())[:10])

    prompt = _LLM_CLASSIFY_PROMPT.format(
        name=place.name or "Unknown",
        tags=tags_str,
        metadata=meta_str or "minimal",
        lat=place.coordinates.lat,
        lng=place.coordinates.lng,
    )

    try:
        raw = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=256,
        )
        json_str = raw.strip()
        if json_str.startswith("```"):
            json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        data = json.loads(json_str)
        cats = []
        for cat_str in data.get("categories", []):
            try:
                cats.append(PlaceCategory(cat_str))
            except ValueError:
                pass

        if cats:
            conf = min(1.0, max(0.3, data.get("confidence", 0.6)))
            place.categories = sorted(cats, key=lambda c: c.value)
            place.category_confidence = {c.value: conf for c in cats}
            place.metadata.pop("_needs_llm_classification", None)
            logger.info("LLM classified '%s' → %s (%.2f)", place.name, [c.value for c in cats], conf)

    except Exception as e:
        logger.warning("LLM classification failed for '%s': %s", place.name, e)
        # Keep rule-based default

    return place


def classify_places(places: list[Place]) -> list[Place]:
    """Batch-classify a list of places using rule-based engine."""
    for p in places:
        classify_place(p)
    return places


async def classify_places_with_llm(places: list[Place]) -> list[Place]:
    """Batch-classify, using LLM fallback for ambiguous places."""
    classify_places(places)  # Rule-based first

    # LLM fallback for unclassified places
    for p in places:
        if p.metadata.get("_needs_llm_classification"):
            try:
                await classify_place_llm(p)
            except Exception:
                pass  # Keep rule-based default

    return places


def _searchable_text(place: Place) -> str:
    """Build a lowercase text blob from all available place info."""
    parts: list[str] = []
    if place.name:
        parts.append(place.name)
    if place.description:
        parts.append(place.description)
    parts.extend(place.tags)
    if "osm_tags" in place.metadata:
        for k, v in place.metadata["osm_tags"].items():
            parts.append(f"{k} {v}")
    return " ".join(parts).lower()
