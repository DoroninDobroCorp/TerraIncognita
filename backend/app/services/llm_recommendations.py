"""Story 2.3 — Contextual Recommendations.

Generates personalized place recommendations based on user preferences,
visit history, and current context (time, weather, etc.).
"""

from __future__ import annotations

import json
import logging

from app.models.chat import (
    RecommendationRequest,
    RecommendationResponse,
    RecommendedPlace,
    UserPreferences,
)
from app.models.place import DiscoverRequest, Place, PlaceCategory
from app.services.discovery import discover
from app.services.llm_client import chat_completion

# Time-of-day → preferred categories for contextual ranking
_TIME_CATEGORY_BOOST: dict[str, set[PlaceCategory]] = {
    "morning": {PlaceCategory.PARK, PlaceCategory.VIEWPOINT, PlaceCategory.NATURE_HIDDEN},
    "afternoon": {PlaceCategory.MUSEUM, PlaceCategory.LANDMARK, PlaceCategory.ARCHITECTURE},
    "evening": {PlaceCategory.VIEWPOINT, PlaceCategory.WATER, PlaceCategory.STREET_ART, PlaceCategory.RESTAURANT_NOTABLE},
    "night": {PlaceCategory.UNDERGROUND, PlaceCategory.ABANDONED, PlaceCategory.STREET_ART},
}

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Terra Incognita's recommendation engine. Your job is to select and rank \
places for a user based on their preferences and context.

Given a list of available places and user preferences, return a JSON array of \
recommendations with reasons. Each recommendation should explain WHY this place \
is relevant to the user.

Diversity rule: Never recommend more than 2 places of the same category in a row. \
Mix unusual and classic places for a balanced experience.

For notable restaurants/hotels: always highlight what makes them exceptional — \
don't just say "good restaurant", explain the story, history, or uniqueness.
"""

RECOMMENDATION_PROMPT = """\
User preferences:
- Favorite categories: {favorite_categories}
- Liked places: {liked_count} places
- Disliked places: {disliked_count} places
- Context: {context}

Available places (top {count}):
{places_json}

Select up to {limit} best recommendations. For each, explain why it's a good match.
Ensure diversity — mix categories, don't suggest 3+ of the same type.

Respond ONLY with valid JSON:
[
  {{
    "place_index": 0,
    "reason": "Brief explanation why this place matches",
    "relevance_score": 0.95
  }}
]
"""


async def get_recommendations(req: RecommendationRequest) -> RecommendationResponse:
    """Generate contextual recommendations."""
    # Determine strategy
    strategy = _determine_strategy(req.preferences)

    # Fetch candidate places from Discovery Engine
    discover_req = DiscoverRequest(
        lat=req.lat,
        lng=req.lng,
        radius_km=req.radius_km,
        categories=req.preferences.favorite_categories if strategy == "personalized" else [],
        exclude_visited=req.preferences.visited_place_ids,
        limit=min(req.limit * 3, 200),  # Fetch more to allow LLM to curate
        sort_by="confidence",
    )

    result = await discover(discover_req)
    candidates = result.places

    if not candidates:
        return RecommendationResponse(
            recommendations=[],
            total=0,
            strategy=strategy,
            language=req.language,
        )

    # For cold start or if no API key, use rule-based ranking
    if strategy == "cold_start":
        ranked = _rule_based_ranking(candidates, req)
        return RecommendationResponse(
            recommendations=ranked[:req.limit],
            total=len(ranked),
            strategy=strategy,
            language=req.language,
        )

    # Use LLM for personalized ranking
    try:
        ranked = await _llm_ranking(candidates, req)
    except Exception:
        logger.exception("LLM ranking failed, falling back to rules")
        ranked = _rule_based_ranking(candidates, req)
        strategy = "diverse"

    return RecommendationResponse(
        recommendations=ranked[:req.limit],
        total=len(ranked),
        strategy=strategy,
        language=req.language,
    )


def _determine_strategy(prefs: UserPreferences) -> str:
    """Decide recommendation strategy based on user data."""
    has_history = bool(prefs.liked_place_ids or prefs.disliked_place_ids)
    has_favorites = bool(prefs.favorite_categories)

    if has_history and has_favorites:
        return "personalized"
    if has_favorites:
        return "personalized"
    if has_history:
        return "diverse"
    return "cold_start"


def _rule_based_ranking(
    candidates: list[Place], req: RecommendationRequest
) -> list[RecommendedPlace]:
    """Rank places using deterministic rules (no LLM needed)."""
    prefs = req.preferences
    fav_set = set(prefs.favorite_categories) if prefs.favorite_categories else None
    liked_set = set(prefs.liked_place_ids)

    scored: list[RecommendedPlace] = []
    category_counts: dict[str, int] = {}

    for place in candidates:
        score = place.confidence
        reasons: list[str] = []

        # Boost if matches favorite categories
        if fav_set:
            overlap = set(place.categories) & fav_set
            if overlap:
                score += 0.2 * len(overlap)
                reasons.append(f"matches your interest in {', '.join(c.value for c in overlap)}")

        # Boost unusual places (core value prop)
        if place.is_unusual:
            score += 0.1
            reasons.append("unusual find")

        # Time-of-day context boosting
        time_of_day = req.context.get("time_of_day")
        if time_of_day:
            time_cats = _TIME_CATEGORY_BOOST.get(time_of_day, set())
            time_overlap = set(place.categories) & time_cats
            if time_overlap:
                score += 0.1
                reasons.append(f"great for {time_of_day}")

        # Diversity penalty: suppress same-category clusters
        for cat in place.categories:
            cat_count = category_counts.get(cat.value, 0)
            if cat_count >= 2:
                score -= 0.15
            category_counts[cat.value] = cat_count + 1

        # Distance factor: slightly prefer closer places
        if place.distance_m and place.distance_m > 5000:
            score -= 0.05

        score = max(0.0, min(1.0, score))
        reason = "; ".join(reasons) if reasons else "interesting place nearby"

        scored.append(RecommendedPlace(
            place=place,
            reason=reason,
            relevance_score=round(score, 2),
        ))

    # Sort by relevance_score descending
    scored.sort(key=lambda r: r.relevance_score, reverse=True)
    return scored


async def _llm_ranking(
    candidates: list[Place], req: RecommendationRequest
) -> list[RecommendedPlace]:
    """Use LLM for intelligent ranking and recommendation reasons."""
    prefs = req.preferences

    # Prepare places summary for LLM (limited to avoid token overflow)
    top_candidates = candidates[:30]
    places_data = []
    for i, p in enumerate(top_candidates):
        places_data.append({
            "index": i,
            "name": p.name or "Unnamed",
            "categories": [c.value for c in p.categories],
            "confidence": p.confidence,
            "distance_m": p.distance_m,
            "is_unusual": p.is_unusual,
            "tags": p.tags[:5],
        })

    context_str = json.dumps(req.context) if req.context else "no specific context"
    fav_str = ", ".join(c.value for c in prefs.favorite_categories) or "none specified"

    prompt = RECOMMENDATION_PROMPT.format(
        favorite_categories=fav_str,
        liked_count=len(prefs.liked_place_ids),
        disliked_count=len(prefs.disliked_place_ids),
        context=context_str,
        count=len(places_data),
        places_json=json.dumps(places_data, ensure_ascii=False),
        limit=req.limit,
    )

    raw = await chat_completion(
        messages=[{"role": "user", "content": prompt}],
        system=SYSTEM_PROMPT,
        temperature=0.5,
        max_tokens=1024,
    )

    # Parse LLM response
    json_str = raw.strip()
    if json_str.startswith("```"):
        json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    recommendations_data = json.loads(json_str)
    results: list[RecommendedPlace] = []

    for rec in recommendations_data:
        idx = rec.get("place_index", 0)
        if 0 <= idx < len(top_candidates):
            results.append(RecommendedPlace(
                place=top_candidates[idx],
                reason=rec.get("reason", "Recommended for you"),
                relevance_score=min(1.0, max(0.0, rec.get("relevance_score", 0.5))),
            ))

    return results
