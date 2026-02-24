"""Story 2.4 — Storytelling Mode (text generation, TTS deferred to V2).

Generates immersive narratives for places on a route: facts + legends + atmosphere.
Text-only — audio/TTS integration is deferred to V2.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.place import Place
from app.services.llm_client import cached_completion

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
"""

STORY_PROMPT = """\
Create an immersive narrative for this place:

Name: {name}
Categories: {categories}
Location: {lat:.4f}, {lng:.4f}
Tags: {tags}
Available info: {metadata}

Write in {language}. Create a compelling story that mixes:
1. Real facts (from the tags/metadata above)
2. Atmospheric description of the place
3. Historical context or local legends (speculate carefully)
4. What the explorer might feel/see when visiting

The story should make the reader want to visit this place RIGHT NOW.
"""

ROUTE_STORY_PROMPT = """\
Create a connecting narrative for a route through these places (in order):

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


async def generate_place_story(
    place: Place,
    language: str = "ru",
) -> dict[str, Any]:
    """Generate an immersive narrative for a single place.

    Returns dict with 'story', 'place_id', 'ai_generated', 'language'.
    """
    cache_key = f"story:{place.id}:{language}"
    lang_name = {"ru": "Russian", "en": "English"}.get(language, language)

    categories_str = ", ".join(c.value for c in place.categories) or "unknown"
    tags_str = ", ".join(place.tags[:15]) or "none"
    meta_str = _format_metadata(place.metadata)

    prompt = STORY_PROMPT.format(
        name=place.name or "Unnamed place",
        categories=categories_str,
        lat=place.coordinates.lat,
        lng=place.coordinates.lng,
        tags=tags_str,
        metadata=meta_str,
        language=lang_name,
    )

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
        logger.exception("Failed to generate story for %s", place.id)
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
    cache_key = f"route_story:{':'.join(place_ids[:10])}:{language}"
    lang_name = {"ru": "Russian", "en": "English"}.get(language, language)

    places_list_parts = []
    for i, p in enumerate(places, 1):
        cats = ", ".join(c.value for c in p.categories)
        meta = _format_metadata(p.metadata)
        places_list_parts.append(
            f"Stop {i}: {p.name or 'Unnamed'} [{cats}]\n"
            f"  Location: {p.coordinates.lat:.4f}, {p.coordinates.lng:.4f}\n"
            f"  Distance from previous: {p.distance_m or 0:.0f}m\n"
            f"  Info: {meta}"
        )

    prompt = ROUTE_STORY_PROMPT.format(
        places_list="\n\n".join(places_list_parts),
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
    """Extract readable metadata for prompts."""
    parts = []
    if "osm_tags" in metadata:
        for key in ["historic", "building", "tourism", "natural", "description",
                     "start_date", "architect", "wikipedia"]:
            if key in metadata["osm_tags"]:
                parts.append(f"{key}: {metadata['osm_tags'][key]}")
    if "wikipedia_url" in metadata:
        parts.append(f"Wikipedia: {metadata['wikipedia_url']}")
    return "; ".join(parts) if parts else "minimal data"


def _fallback_story(place: Place, language: str) -> dict[str, Any]:
    """Fallback when LLM is unavailable."""
    name = place.name or "This place"
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
    names = [p.name or "unnamed stop" for p in places]
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
