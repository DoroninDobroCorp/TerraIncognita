"""Story 2.2 — Place Description Generation.

Generates atmospheric, engaging descriptions for places using LLM,
with practical info (how to get there, best time, what to bring).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.models.chat import DescriptionRequest, DescriptionResponse
from app.services import llm_client
from app.services.llm_client import cached_completion
from app.utils.sanitize import (
    UNTRUSTED_DATA_NOTICE,
    fence_untrusted,
    format_tag_metadata,
    sanitize_label,
    sanitize_user_input,
)

_METADATA_KEYS = (
    "historic", "building", "tourism", "natural", "man_made",
    "military", "abandoned", "description", "wikipedia",
)
MAX_SECTION_CHARS = 4_000  # the LLM answer is shown to users: bound it

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
""" + UNTRUSTED_DATA_NOTICE

DESCRIPTION_PROMPT = """\
Generate an engaging description for the place in the data block below:

{place_data}

Write in {language}. Structure your response as:

DESCRIPTION:
(2-3 atmospheric paragraphs about the place, its character, what makes it special)

PRACTICAL:
(1 paragraph: best time to visit, what to bring, how to approach, any warnings)
"""


def _cache_key(req: DescriptionRequest, fields: dict[str, str]) -> str:
    """Cache key over everything that shapes the prompt.

    It used to be ``desc:<place_id>:<language>`` only, while name, tags and metadata come
    from the *client*: whoever asked first for an id (with whatever text) decided the
    description every later user got for 7 days (cache poisoning / stored prompt injection).
    """
    payload = json.dumps(
        [fields, round(req.lat, 4), round(req.lng, 4)], sort_keys=True, ensure_ascii=False
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"desc:{req.place_id}:{req.language}:{digest}"


async def _is_cached(cache_key: str) -> bool:
    try:
        return await llm_client._description_cache.get(cache_key) is not None
    except Exception:
        return False


async def generate_description(req: DescriptionRequest) -> DescriptionResponse:
    """Generate an atmospheric AI description for a place."""
    lang_name = {"ru": "Russian", "en": "English"}.get(req.language, req.language)
    if req.language == "auto":
        lang_name = "Russian"  # Default to Russian for the primary user

    # Everything below is client supplied: single-line, bounded, fenced as data.
    fields = {
        "name": sanitize_label(req.place_name or "Unnamed place", 300),
        "categories": ", ".join(c.value for c in req.place_categories) or "unknown",
        "tags": ", ".join(sanitize_label(t, 100) for t in req.place_tags[:20]) or "none",
        "metadata": _extract_useful_metadata(req.place_metadata),
    }
    place_data = fence_untrusted("place_data", (
        f"Name: {fields['name']}\n"
        f"Categories: {fields['categories']}\n"
        f"Location: {req.lat:.4f}, {req.lng:.4f}\n"
        f"Tags: {fields['tags']}\n"
        f"Available metadata: {fields['metadata']}"
    ))
    prompt = DESCRIPTION_PROMPT.format(place_data=place_data, language=lang_name)
    cache_key = _cache_key(req, fields)

    try:
        was_cached = await _is_cached(cache_key)
        raw = await cached_completion(
            cache_key=cache_key,
            messages=[{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
            temperature=0.8,
            max_tokens=800,
        )

        description, practical = _parse_description_response(raw)

        return DescriptionResponse(
            place_id=req.place_id,
            description=description,
            practical_info=practical,
            ai_generated=True,
            cached=was_cached,
            language=req.language,
        )
    except Exception:
        logger.exception("Failed to generate description for %s", req.place_id)
        return _fallback_description(req)


def _parse_description_response(raw: str) -> tuple[str, str | None]:
    """Split LLM response into description and practical info.

    The answer is untrusted text: control characters are dropped, each part is bounded, and
    the sections are located independently of their order (a PRACTICAL block before the
    DESCRIPTION block used to produce an empty description).
    """
    raw = sanitize_user_input(raw or "", max_length=None)

    desc_marker = "DESCRIPTION:"
    prac_marker = "PRACTICAL:"
    desc_at = raw.find(desc_marker)
    prac_at = raw.find(prac_marker)

    if desc_at != -1 and prac_at != -1:
        if desc_at < prac_at:
            description = raw[desc_at + len(desc_marker):prac_at]
            practical = raw[prac_at + len(prac_marker):]
        else:
            practical = raw[prac_at + len(prac_marker):desc_at]
            description = raw[desc_at + len(desc_marker):]
        return description.strip()[:MAX_SECTION_CHARS], practical.strip()[:MAX_SECTION_CHARS]

    # Fallback: treat everything as description
    return raw[:MAX_SECTION_CHARS], None


def _extract_useful_metadata(metadata: dict[str, Any]) -> str:
    """Extract human-readable metadata for the LLM prompt (any input shape is tolerated)."""
    return format_tag_metadata(metadata, _METADATA_KEYS, include_atlas=True) or "minimal data available"


def _fallback_description(req: DescriptionRequest) -> DescriptionResponse:
    """Fallback when LLM is unavailable."""
    name = sanitize_label(req.place_name or "This place", 300)
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
