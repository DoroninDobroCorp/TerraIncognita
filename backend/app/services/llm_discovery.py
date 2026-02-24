"""Story 2.1 + 2.5 — Natural Language Discovery & Smart Query Understanding.

Parses natural language queries into structured intents,
runs discovery, and generates conversational responses.
"""

from __future__ import annotations

import json
import logging
import uuid

from app.models.chat import ChatRequest, ChatResponse, ParsedIntent
from app.models.place import DiscoverRequest, Place, PlaceCategory
from app.services.discovery import discover
from app.services.llm_client import chat_completion
from app.utils.sanitize import sanitize_for_log, sanitize_user_input

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Terra Incognita — an AI exploration companion that helps travelers \
discover everything truly interesting around them. Your main superpower is finding \
hidden gems: abandoned buildings, underground tunnels, secret viewpoints, street art, \
ruins — places most people walk right past. But you also know classic landmarks \
and can recommend truly outstanding restaurants and hotels — ONLY the ones with \
a story, history, or something genuinely exceptional about them.

Your philosophy: show everything worth discovering. Hidden places are the core, \
classic landmarks add context, and outstanding restaurants/hotels are the cherry \
on top — but ONLY if they are remarkable. A Michelin-starred restaurant in a \
medieval cellar? Yes. A generic pizza place? Never. A hotel where Hemingway wrote? \
Yes. A Holiday Inn? Never. Always explain WHY something is special.

Your role:
1. Parse the user's natural language query into a structured search intent.
2. After places are found, describe them in an engaging, atmospheric way.
3. Be helpful, enthusiastic about exploration, but honest about safety concerns.
4. Support Russian, English, and other languages — respond in the user's language.
5. Understand mood-based queries like "something like Stalker", "romantic but not cliché".
6. For restaurants/hotels: always explain what makes them outstanding.

Available place categories:
- abandoned, underground, industrial, ruins, military, nature_hidden, viewpoint, \
street_art, transport, water, cave (unusual/hidden)
- religious, landmark, museum, architecture, monument, park (classic)
- restaurant_notable, hotel_notable (only truly exceptional — historic, award-winning, \
architecturally unique, or culturally significant)

Mood/atmosphere mappings:
- "creepy/spooky/stalker" → abandoned, industrial, ruins, underground, military
- "romantic" → viewpoint, nature_hidden, water, park, architecture, restaurant_notable
- "historic/old" → ruins, military, religious, landmark, monument, architecture
- "artistic/creative" → street_art, museum, architecture
- "nature/wild" → cave, nature_hidden, water, viewpoint
- "adventure/extreme" → underground, cave, abandoned, military
- "family-friendly/safe" → park, museum, landmark, viewpoint, monument
- "foodie/gourmet" → restaurant_notable, landmark
- "luxury/unique stay" → hotel_notable, architecture
"""

INTENT_EXTRACTION_PROMPT = """\
Parse the user's message into a JSON search intent. Respond ONLY with valid JSON, \
no extra text.

{
  "categories": ["category1", "category2"],
  "mood": ["mood_keyword1"],
  "terrain": ["urban", "nature", "coastal", "mountain"],
  "distance_preference": "nearby|walkable|any",
  "time_of_day": "morning|afternoon|evening|night|any",
  "keywords": ["specific_keyword1"],
  "response_language": "ru|en|detected_language"
}

Rules:
- categories MUST be from: abandoned, underground, industrial, ruins, military, \
nature_hidden, viewpoint, street_art, transport, water, cave, religious, landmark, \
museum, architecture, monument, park, restaurant_notable, hotel_notable
- If query is vague, pick 3-5 most likely categories
- If query mentions mood (e.g. "creepy"), map to appropriate categories
- restaurant_notable / hotel_notable: ONLY when user explicitly asks about \
exceptional dining or unique accommodation, never for generic food/hotel queries
- Detect the language of the user's message
"""

RESPONSE_PROMPT = """\
The user asked: "{query}"

Found {count} places matching their intent. Here are the top results:
{places_summary}

Write an engaging, conversational response in {language}:
1. Briefly acknowledge what they're looking for
2. Highlight the 2-3 most interesting finds with atmospheric mini-descriptions
3. If no places found, suggest broadening the search or trying different keywords
4. Keep it concise (3-5 sentences for the highlights)
5. Do NOT use markdown formatting — write plain text with natural flow
"""


async def process_chat(req: ChatRequest) -> ChatResponse:
    """Process a natural language discovery query.

    Pipeline:
    1. Extract structured intent from user message via LLM
    2. Run Discovery Engine with extracted categories
    3. Generate conversational response about found places
    """
    conversation_id = req.conversation_id or str(uuid.uuid4())

    # Sanitize user input
    safe_message = sanitize_user_input(req.message)
    logger.info("Chat query: %s", sanitize_for_log(safe_message))

    # 1. Extract intent
    intent = await _extract_intent(safe_message, req.history)

    # 2. Build and run discovery request
    discover_req = DiscoverRequest(
        lat=req.lat,
        lng=req.lng,
        radius_km=req.radius_km,
        categories=intent.categories,
        limit=10,
        sort_by="confidence",
    )
    result = await discover(discover_req)

    # 3. Generate response
    detected_lang = req.language if req.language != "auto" else _detect_language(intent)
    response_text = await _generate_response(
        query=safe_message,
        places=result.places,
        language=detected_lang,
        history=req.history,
    )

    # 4. Generate follow-up suggestions
    suggestions = _generate_suggestions(intent, result.places, detected_lang)

    return ChatResponse(
        message=response_text,
        places=result.places,
        parsed_intent=intent,
        conversation_id=conversation_id,
        language=detected_lang,
        suggested_questions=suggestions,
    )


async def _extract_intent(
    message: str, history: list | None = None
) -> ParsedIntent:
    """Use LLM to parse natural language into structured intent."""
    messages = []

    # Add conversation history for context
    if history:
        for h in history[-6:]:  # Last 6 messages for context
            messages.append({"role": h.role, "content": h.content})

    messages.append({"role": "user", "content": message})

    try:
        raw = await chat_completion(
            messages=messages,
            system=INTENT_EXTRACTION_PROMPT,
            temperature=0.3,
            max_tokens=512,
        )

        # Parse JSON from response (handle potential markdown wrapping)
        json_str = raw.strip()
        if json_str.startswith("```"):
            json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        data = json.loads(json_str)

        # Map category strings to enum values
        valid_cats = []
        for cat in data.get("categories", []):
            try:
                valid_cats.append(PlaceCategory(cat))
            except ValueError:
                logger.debug("Unknown category from LLM: %s", cat)

        return ParsedIntent(
            categories=valid_cats,
            mood=data.get("mood", []),
            terrain=data.get("terrain", []),
            distance_preference=data.get("distance_preference"),
            time_of_day=data.get("time_of_day"),
            keywords=data.get("keywords", []),
            original_query=message,
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse LLM intent, using fallback: %s", e)
        return _fallback_intent(message)
    except Exception as e:
        logger.warning("LLM intent extraction failed, using fallback: %s", e)
        return _fallback_intent(message)


def _fallback_intent(message: str) -> ParsedIntent:
    """Rule-based fallback when LLM intent parsing fails."""
    text = message.lower()
    categories: list[PlaceCategory] = []
    mood: list[str] = []

    # Mood-based mapping
    mood_map: dict[str, list[PlaceCategory]] = {
        "заброш": [PlaceCategory.ABANDONED],
        "abandon": [PlaceCategory.ABANDONED],
        "подземн": [PlaceCategory.UNDERGROUND],
        "underground": [PlaceCategory.UNDERGROUND],
        "tunnel": [PlaceCategory.UNDERGROUND],
        "тоннел": [PlaceCategory.UNDERGROUND],
        "руин": [PlaceCategory.RUINS],
        "ruin": [PlaceCategory.RUINS],
        "сталкер": [PlaceCategory.ABANDONED, PlaceCategory.INDUSTRIAL],
        "stalker": [PlaceCategory.ABANDONED, PlaceCategory.INDUSTRIAL],
        "жутк": [PlaceCategory.ABANDONED, PlaceCategory.UNDERGROUND],
        "creepy": [PlaceCategory.ABANDONED, PlaceCategory.UNDERGROUND],
        "романтич": [PlaceCategory.VIEWPOINT, PlaceCategory.NATURE_HIDDEN],
        "romantic": [PlaceCategory.VIEWPOINT, PlaceCategory.NATURE_HIDDEN],
        "пещер": [PlaceCategory.CAVE],
        "cave": [PlaceCategory.CAVE],
        "воен": [PlaceCategory.MILITARY],
        "military": [PlaceCategory.MILITARY],
        "bunker": [PlaceCategory.MILITARY],
        "бункер": [PlaceCategory.MILITARY],
        "вид": [PlaceCategory.VIEWPOINT],
        "view": [PlaceCategory.VIEWPOINT],
        "музей": [PlaceCategory.MUSEUM],
        "museum": [PlaceCategory.MUSEUM],
        "церк": [PlaceCategory.RELIGIOUS],
        "church": [PlaceCategory.RELIGIOUS],
        "храм": [PlaceCategory.RELIGIOUS],
        "вод": [PlaceCategory.WATER],
        "water": [PlaceCategory.WATER],
        "граффити": [PlaceCategory.STREET_ART],
        "graffiti": [PlaceCategory.STREET_ART],
        "street art": [PlaceCategory.STREET_ART],
        "ресторан": [PlaceCategory.RESTAURANT_NOTABLE],
        "restaurant": [PlaceCategory.RESTAURANT_NOTABLE],
        "кухн": [PlaceCategory.RESTAURANT_NOTABLE],
        "еда": [PlaceCategory.RESTAURANT_NOTABLE],
        "foodie": [PlaceCategory.RESTAURANT_NOTABLE],
        "gourmet": [PlaceCategory.RESTAURANT_NOTABLE],
        "michelin": [PlaceCategory.RESTAURANT_NOTABLE],
        "мишлен": [PlaceCategory.RESTAURANT_NOTABLE],
        "отель": [PlaceCategory.HOTEL_NOTABLE],
        "hotel": [PlaceCategory.HOTEL_NOTABLE],
        "гостиниц": [PlaceCategory.HOTEL_NOTABLE],
    }

    for keyword, cats in mood_map.items():
        if keyword in text:
            categories.extend(cats)
            mood.append(keyword)

    # If nothing matched, broad search
    if not categories:
        categories = [
            PlaceCategory.ABANDONED,
            PlaceCategory.VIEWPOINT,
            PlaceCategory.RUINS,
            PlaceCategory.LANDMARK,
        ]

    return ParsedIntent(
        categories=list(set(categories)),
        mood=mood,
        original_query=text,
    )


async def _generate_response(
    query: str,
    places: list[Place],
    language: str,
    history: list | None = None,
) -> str:
    """Generate a conversational response about found places."""
    if not places:
        places_summary = "No places found in this area matching the criteria."
    else:
        lines = []
        for i, p in enumerate(places[:5], 1):
            cats = ", ".join(c.value for c in p.categories)
            dist = f"{p.distance_m:.0f}m" if p.distance_m else "?"
            lines.append(
                f"{i}. {p.name or 'Unnamed'} [{cats}] — {dist} away, "
                f"confidence: {p.confidence:.2f}"
            )
        places_summary = "\n".join(lines)

    lang_name = {"ru": "Russian", "en": "English"}.get(language, language)

    messages = []
    if history:
        for h in history[-4:]:
            messages.append({"role": h.role, "content": h.content})

    messages.append({
        "role": "user",
        "content": RESPONSE_PROMPT.format(
            query=query,
            count=len(places),
            places_summary=places_summary,
            language=lang_name,
        ),
    })

    try:
        return await chat_completion(
            messages=messages,
            system=SYSTEM_PROMPT,
            temperature=0.7,
            max_tokens=800,
        )
    except Exception:
        logger.exception("Failed to generate LLM response, using fallback")
        return _fallback_response(places, language)


def _fallback_response(places: list[Place], language: str) -> str:
    """Simple response when LLM is unavailable."""
    if not places:
        if language == "ru":
            return "К сожалению, не удалось найти места по вашему запросу. Попробуйте расширить радиус поиска."
        return "No places found matching your query. Try expanding the search radius."

    count = len(places)
    if language == "ru":
        return f"Найдено {count} мест по вашему запросу. Вот самые интересные из них."
    return f"Found {count} places matching your query. Here are the most interesting ones."


def _detect_language(intent: ParsedIntent) -> str:
    """Detect language from intent keywords."""
    text = intent.original_query.lower()
    cyrillic_count = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    if cyrillic_count > len(text) * 0.3:
        return "ru"
    return "en"


def _generate_suggestions(
    intent: ParsedIntent, places: list[Place], language: str
) -> list[str]:
    """Generate contextual follow-up question suggestions (no LLM call)."""
    suggestions: list[str] = []
    cats = {c.value for c in intent.categories}

    if language == "ru":
        if places:
            suggestions.append("Покажи что-нибудь поближе")
            if "abandoned" not in cats:
                suggestions.append("А есть заброшки рядом?")
            if "viewpoint" not in cats:
                suggestions.append("Где лучший вид?")
            suggestions.append("Построй маршрут через эти точки")
        else:
            suggestions.append("Расширь радиус поиска")
            suggestions.append("Покажи всё интересное рядом")
            suggestions.append("Что-нибудь необычное в 10 км")
    else:
        if places:
            suggestions.append("Show me something closer")
            if "abandoned" not in cats:
                suggestions.append("Any abandoned places nearby?")
            if "viewpoint" not in cats:
                suggestions.append("Where's the best view?")
            suggestions.append("Build a route through these")
        else:
            suggestions.append("Expand search radius")
            suggestions.append("Show me everything interesting nearby")
            suggestions.append("Something unusual within 10 km")

    return suggestions[:3]
