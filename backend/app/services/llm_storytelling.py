"""Story 2.4 — Storytelling Mode (text generation, TTS deferred to V2).

Generates immersive narratives for places on a route: facts + legends + atmosphere.
Text-only — audio/TTS integration is deferred to V2.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.models.place import Place
from app.services.llm_client import cached_completion
from app.utils.geo import haversine_distance_m
from app.utils.sanitize import (
    UNTRUSTED_DATA_NOTICE,
    fence_untrusted,
    format_tag_metadata,
    sanitize_label,
)

_METADATA_KEYS = (
    "historic", "building", "tourism", "natural", "description",
    "start_date", "architect", "wikipedia",
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a master storyteller for Terra Incognita, an exploration app. \
Your job is to create captivating narratives about places that explorers visit — \
from abandoned ruins and hidden caves to outstanding restaurants and historic hotels.

Your storytelling style:
- Mix verified facts with local legends and atmospheric description
- Paint a vivid picture — what did this place look like 50 or 100 years ago?
- Weave in mystery and intrigue — what secrets might these walls hold?
- For notable restaurants/hotels: tell the human story — who founded it, \
  who dined here, what makes the experience unforgettable
- Keep it grounded — don't fabricate specific historical events, but you can \
  speculate about atmosphere and context
- Structure: Hook → History/Facts → Legends/Mystery → Present atmosphere → Invitation
- Length: 3-5 paragraphs (enough for ~2 min reading)
- Mark speculative content with phrases like "legend says", "locals whisper", \
  "one can imagine"

IMPORTANT: These are meant to be read aloud (future TTS). Use short sentences, \
natural pauses, and conversational rhythm. Avoid bullet points or formatting.
""" + UNTRUSTED_DATA_NOTICE

STORY_PROMPT = """\
Create an immersive narrative for the place in the data block below:

{place_data}

Write in {language}. Create a compelling story that mixes:
1. Real facts (from the tags/metadata above)
2. Atmospheric description of the place
3. Historical context or local legends (speculate carefully)
4. What the explorer might feel/see when visiting

The story should make the reader want to visit this place RIGHT NOW.
"""

ROUTE_STORY_PROMPT = """\
Create a connecting narrative for a route through the places in the data block below (in order):

{places_list}

Write in {language}. Create a journey narrative that:
1. Introduces the route as an adventure
2. Briefly describes each stop with a hook
3. Creates transitions between stops ("as you leave X, heading toward Y...")
4. Builds anticipation for the next stop
5. Ends with a satisfying conclusion

Keep each stop description to 2-3 sentences. The whole narrative should feel \
like a guided adventure, not a list.
"""


def _place_fingerprint(place: Place) -> dict[str, Any]:
    """Everything about a place that ends up in a prompt (and so must be part of a cache key)."""
    return {
        "id": place.id,
        "name": sanitize_label(place.name or "", 300),
        "categories": sorted(c.value for c in place.categories),
        "tags": [sanitize_label(t, 100) for t in place.tags[:15]],
        "meta": _format_metadata(place.metadata),
        "lat": round(place.coordinates.lat, 4),
        "lng": round(place.coordinates.lng, 4),
    }


def _digest(*parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def generate_place_story(
    place: Place,
    language: str = "ru",
) -> dict[str, Any]:
    """Generate an immersive narrative for a single place.

    Returns dict with 'story', 'place_id', 'ai_generated', 'language'.
    """
    # The key covers every prompt input: name/tags/metadata are client supplied, so a key
    # of just <place_id>:<language> let the first caller poison the story for everybody.
    cache_key = f"story:{place.id}:{language}:{_digest(_place_fingerprint(place))}"
    lang_name = {"ru": "Russian", "en": "English"}.get(language, language)

    categories_str = ", ".join(c.value for c in place.categories) or "unknown"
    tags_str = ", ".join(sanitize_label(t, 100) for t in place.tags[:15]) or "none"
    meta_str = _format_metadata(place.metadata)
    place_data = fence_untrusted("place_data", (
        f"Name: {sanitize_label(place.name or 'Unnamed place', 300)}\n"
        f"Categories: {categories_str}\n"
        f"Location: {place.coordinates.lat:.4f}, {place.coordinates.lng:.4f}\n"
        f"Tags: {tags_str}\n"
        f"Available info: {meta_str}"
    ))
    prompt = STORY_PROMPT.format(place_data=place_data, language=lang_name)

    try:
        story = await cached_completion(
            cache_key=cache_key,
            messages=[{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
            temperature=0.85,
            max_tokens=1200,
        )
        return {
            "story": story,
            "place_id": place.id,
            "place_name": place.name,
            "ai_generated": True,
            "language": language,
        }
    except Exception:
        logger.exception("Failed to generate story for %r", place.id)
        return _fallback_story(place, language)


async def generate_route_story(
    places: list[Place],
    language: str = "ru",
) -> dict[str, Any]:
    """Generate a connecting narrative for a multi-stop route.

    Returns dict with 'story', 'place_ids', 'ai_generated', 'language'.
    """
    if not places:
        return {"story": "", "place_ids": [], "ai_generated": False, "language": language}

    place_ids = [p.id for p in places]
    # All stops (not the first 10) and their prompt-relevant data: routes that shared their
    # first ten places used to get each other's story, with the wrong stops.
    cache_key = f"route_story:{_digest([_place_fingerprint(p) for p in places])}:{language}"
    lang_name = {"ru": "Russian", "en": "English"}.get(language, language)

    places_list_parts = []
    previous = None
    for i, p in enumerate(places, 1):
        cats = ", ".join(c.value for c in p.categories)
        meta = _format_metadata(p.metadata)
        # Callers build these Places from a request, so distance_m is normally unset: use the
        # real leg length instead of telling the model every stop is "0 m" from the previous one.
        if previous is not None:
            leg_m = haversine_distance_m(
                previous.coordinates.lat, previous.coordinates.lng,
                p.coordinates.lat, p.coordinates.lng,
            )
            leg = f"  Distance from previous: {leg_m:.0f}m\n"
        else:
            leg = ""
        places_list_parts.append(
            f"Stop {i}: {sanitize_label(p.name or 'Unnamed', 300)} [{cats}]\n"
            f"  Location: {p.coordinates.lat:.4f}, {p.coordinates.lng:.4f}\n"
            f"{leg}"
            f"  Info: {meta}"
        )
        previous = p

    prompt = ROUTE_STORY_PROMPT.format(
        places_list=fence_untrusted("place_data", "\n\n".join(places_list_parts)),
        language=lang_name,
    )

    try:
        story = await cached_completion(
            cache_key=cache_key,
            messages=[{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
            temperature=0.85,
            max_tokens=2000,
        )
        return {
            "story": story,
            "place_ids": place_ids,
            "ai_generated": True,
            "language": language,
        }
    except Exception:
        logger.exception("Failed to generate route story")
        return _fallback_route_story(places, language)


def _format_metadata(metadata: dict[str, Any]) -> str:
    """Extract readable metadata for prompts (any input shape is tolerated)."""
    return format_tag_metadata(metadata, _METADATA_KEYS) or "minimal data"


def _fallback_story(place: Place, language: str) -> dict[str, Any]:
    """Fallback when LLM is unavailable."""
    name = sanitize_label(place.name or "This place", 300)
    cats = ", ".join(c.value for c in place.categories)

    if language == "ru":
        story = (
            f"{name} — место категории {cats}, ждущее своего исследователя. "
            f"Посетите его, чтобы открыть для себя его уникальный характер и атмосферу."
        )
    else:
        story = (
            f"{name} — a {cats} location waiting for its explorer. "
            f"Visit to discover its unique character and atmosphere firsthand."
        )

    return {
        "story": story,
        "place_id": place.id,
        "place_name": place.name,
        "ai_generated": False,
        "language": language,
    }


def _fallback_route_story(places: list[Place], language: str) -> dict[str, Any]:
    """Fallback route story when LLM is unavailable."""
    names = [sanitize_label(p.name or "unnamed stop", 300) for p in places]
    place_ids = [p.id for p in places]

    if language == "ru":
        story = f"Маршрут через {len(places)} точек: {', '.join(names)}. Исследуйте каждую!"
    else:
        story = f"A route through {len(places)} stops: {', '.join(names)}. Explore each one!"

    return {
        "story": story,
        "place_ids": place_ids,
        "ai_generated": False,
        "language": language,
    }
