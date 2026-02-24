"""Story 2.2 — Place Description Generation.

Generates atmospheric, engaging descriptions for places using LLM,
with practical info (how to get there, best time, what to bring).
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.chat import DescriptionRequest, DescriptionResponse
from app.services.llm_client import cached_completion

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a travel writer for Terra Incognita, an app that helps explorers \
discover unusual and hidden places, classic landmarks, and truly outstanding \
restaurants and hotels.

Your writing style:
- Atmospheric and immersive — paint a picture of the place
- Honest — don't over-romanticize dangerous or disappointing places
- Practical — include useful info naturally woven into the narrative
- Concise — 2-3 paragraphs max for description, 1 paragraph for practical info
- Adapt to the type of place: mysterious for ruins, respectful for religious sites, \
  exciting for hidden spots, passionate for outstanding restaurants/hotels
- For notable restaurants/hotels: always explain WHAT makes them exceptional — \
  history, architecture, famous guests, unique cuisine, awards

IMPORTANT: Add an "AI-generated" awareness — these descriptions are based on \
available data and may not reflect current conditions.
"""

DESCRIPTION_PROMPT = """\
Generate an engaging description for this place:

Name: {name}
Categories: {categories}
Location: {lat:.4f}, {lng:.4f}
Tags: {tags}
Available metadata: {metadata}

Write in {language}. Structure your response as:

DESCRIPTION:
(2-3 atmospheric paragraphs about the place, its character, what makes it special)

PRACTICAL:
(1 paragraph: best time to visit, what to bring, how to approach, any warnings)
"""


async def generate_description(req: DescriptionRequest) -> DescriptionResponse:
    """Generate an atmospheric AI description for a place."""
    cache_key = f"desc:{req.place_id}:{req.language}"
    lang_name = {"ru": "Russian", "en": "English"}.get(req.language, req.language)
    if req.language == "auto":
        lang_name = "Russian"  # Default to Russian for the primary user

    categories_str = ", ".join(c.value for c in req.place_categories) or "unknown"
    tags_str = ", ".join(req.place_tags[:20]) or "none"

    # Filter metadata to useful fields
    useful_meta = _extract_useful_metadata(req.place_metadata)

    prompt = DESCRIPTION_PROMPT.format(
        name=req.place_name or "Unnamed place",
        categories=categories_str,
        lat=req.lat,
        lng=req.lng,
        tags=tags_str,
        metadata=useful_meta,
        language=lang_name,
    )

    try:
        raw = await cached_completion(
            cache_key=cache_key,
            messages=[{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
            temperature=0.8,
            max_tokens=800,
        )

        description, practical = _parse_description_response(raw)
        is_cached = False  # cached_completion handles this internally

        return DescriptionResponse(
            place_id=req.place_id,
            description=description,
            practical_info=practical,
            ai_generated=True,
            cached=is_cached,
            language=req.language,
        )
    except Exception:
        logger.exception("Failed to generate description for %s", req.place_id)
        return _fallback_description(req)


def _parse_description_response(raw: str) -> tuple[str, str | None]:
    """Split LLM response into description and practical info."""
    raw = raw.strip()

    # Try to find structured sections
    desc_marker = "DESCRIPTION:"
    prac_marker = "PRACTICAL:"

    if desc_marker in raw and prac_marker in raw:
        desc_start = raw.index(desc_marker) + len(desc_marker)
        prac_start = raw.index(prac_marker)
        description = raw[desc_start:prac_start].strip()
        practical = raw[prac_start + len(prac_marker):].strip()
        return description, practical

    # Fallback: treat everything as description
    return raw, None


def _extract_useful_metadata(metadata: dict[str, Any]) -> str:
    """Extract human-readable metadata for the LLM prompt."""
    useful_parts = []

    if "osm_tags" in metadata:
        tags = metadata["osm_tags"]
        for key in ["historic", "building", "tourism", "natural", "man_made",
                     "military", "abandoned", "description", "wikipedia"]:
            if key in tags:
                useful_parts.append(f"{key}: {tags[key]}")

    if "wikipedia_url" in metadata:
        useful_parts.append(f"Wikipedia: {metadata['wikipedia_url']}")

    if "atlas_categories" in metadata:
        useful_parts.append(f"Atlas Obscura categories: {metadata['atlas_categories']}")

    return "; ".join(useful_parts) if useful_parts else "minimal data available"


def _fallback_description(req: DescriptionRequest) -> DescriptionResponse:
    """Fallback when LLM is unavailable."""
    name = req.place_name or "This place"
    cats = ", ".join(c.value for c in req.place_categories) if req.place_categories else "interesting"

    return DescriptionResponse(
        place_id=req.place_id,
        description=f"{name} — a {cats} location waiting to be explored. "
                    f"Visit to discover its unique character firsthand.",
        practical_info="Check current conditions before visiting. Bring appropriate gear.",
        ai_generated=False,
        cached=False,
        language=req.language,
    )
