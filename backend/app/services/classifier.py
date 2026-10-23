"""Story 1.5 — Category Classification.

Rule-based primary classification (fast, deterministic) with
LLM-based fallback for ambiguous or untagged places.
"""

from __future__ import annotations

import json
import logging
import re

from app.models.place import Place, PlaceCategory

logger = logging.getLogger(__name__)

# ── Rule-based keyword → category mapping ──────────────────────────
#
# Matching is word-based, never raw substring: ``park`` must not fire on "parking", ``dam`` on
# "Adam", ``arch`` on "search". A keyword ending in ``*`` is a stem ("abandon*" → abandoned,
# abandonment); any other keyword matches the whole word, optionally pluralised.
# Cyrillic and a few Latin-script Slavic/Romance/Germanic stems are included so places that only
# carry a local-language name or description still get classified.

_KEYWORD_RULES: list[tuple[list[str], list[PlaceCategory]]] = [
    # Unusual
    (["abandon*", "derelict*", "disused", "vacant", "deserted", "ghost town",
      "заброшен*", "покинут*", "закинут*", "напуштен*", "napušten*", "napusten*",
      "verlassen*", "abandonn*", "abandonad*", "abbandonat*", "opuszczon*"],
     [PlaceCategory.ABANDONED]),
    (["ruin*", "collapsed", "руин*", "развалин*", "руїн*", "ruševin*", "rusevin*", "ruiny",
      "ruine*"],
     [PlaceCategory.RUINS]),
    (["tunnel*", "underground", "subway", "metro", "catacomb*", "subterranean",
      "тоннел*", "туннел*", "тунел*", "подземн*", "підземн*", "катакомб*", "tunel*", "podzemn*"],
     [PlaceCategory.UNDERGROUND]),
    (["cave", "grotto*", "cavern*", "karst*", "пещер*", "печер*", "špilj*", "spilj*", "pećin*",
      "pecin*", "jaskini*", "höhle", "grotte"],
     [PlaceCategory.CAVE, PlaceCategory.UNDERGROUND]),
    (["bunker*", "pillbox*", "air raid shelter", "air-raid shelter", "bomb shelter",
      "military", "barracks", "fortification*", "бункер*", "казарм*", "военн*", "военный",
      "військов*", "schron*", "kasarn*", "vojn*"],
     [PlaceCategory.MILITARY]),
    (["factory", "factories", "industrial", "warehouse*", "silo*", "mill", "plant", "chimney*",
      "завод*", "фабрик*", "склад", "elevator*"],
     [PlaceCategory.INDUSTRIAL]),
    (["mine", "mineshaft", "colliery", "adit", "шахт*", "рудник*", "копальн*", "rudnik*"],
     [PlaceCategory.INDUSTRIAL, PlaceCategory.UNDERGROUND]),
    (["quarry", "карьер*", "каменолом*", "kamenolom*"],
     [PlaceCategory.INDUSTRIAL]),
    (["graffiti", "street art", "street-art", "mural*", "stencil*", "граффити", "мурал*",
      "стрит-арт"],
     [PlaceCategory.STREET_ART]),
    (["viewpoint*", "lookout*", "panorama*", "observation", "смотров*", "панорам*",
      "vidikovac*", "razgledn*"],
     [PlaceCategory.VIEWPOINT]),
    (["rail", "railway*", "train", "locomotive*", "station", "depot", "tram",
      "вокзал*", "паровоз*", "локомотив*", "депо", "železnič*", "zeleznic*"],
     [PlaceCategory.TRANSPORT]),
    (["waterfall*", "spring", "dam", "aqueduct*", "reservoir*", "водопад*", "родник*",
      "плотин*", "водохранилищ*", "vodopad*", "izvor*"],
     [PlaceCategory.WATER]),
    (["hidden", "secret", "secluded", "off-trail"],
     [PlaceCategory.NATURE_HIDDEN]),

    # Classic
    (["castle*", "palace*", "fortress*", "citadel*", "замок", "замк*", "дворец", "дворц*",
      "крепост*", "тврђав*", "цитадел*", "twierdz*", "tvrđav*", "tvrdjav*", "tvrdav*"],
     [PlaceCategory.LANDMARK, PlaceCategory.ARCHITECTURE]),
    (["fort", "форт"],
     [PlaceCategory.LANDMARK, PlaceCategory.MILITARY]),
    (["church*", "cathedral*", "chapel*", "mosque*", "synagogue*", "temple*", "monastery",
      "monasteries", "церк*", "церков*", "собор*", "храм*", "монастыр*", "мечет*", "синагог*",
      "crkv*", "manastir*", "kostel*"],
     [PlaceCategory.RELIGIOUS]),
    (["museum*", "gallery", "exhibition", "музей*", "галере*", "muzej*"],
     [PlaceCategory.MUSEUM]),
    (["monument*", "memorial*", "statue*", "obelisk*", "памятник*", "пам'ятник*", "монумент*",
      "мемориал*", "spomenik*"],
     [PlaceCategory.MONUMENT]),
    (["park", "garden*", "botanical", "парк*", "ботанич*"],
     [PlaceCategory.PARK]),
    (["bridge", "lighthouse", "tower", "gate", "arch"],
     [PlaceCategory.ARCHITECTURE]),

    # Notable restaurants & hotels (keywords indicating exceptional places)
    (["michelin", "starred", "historic restaurant", "legendary restaurant",
      "historic hotel", "heritage hotel", "palace hotel", "boutique hotel"],
     [PlaceCategory.RESTAURANT_NOTABLE, PlaceCategory.HOTEL_NOTABLE]),

    # Deep research categories
    (["shipwreck*", "wreck", "diving", "snorkeling", "amphora*", "underwater",
      "submerged", "seabed"],
     [PlaceCategory.UNDERWATER]),
    (["restaurant", "cuisine", "dish", "wine", "beer", "tasting", "culinary",
      "food", "tavern", "konoba"],
     [PlaceCategory.CULINARY]),
]


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    kw = keyword.lower()
    if kw.endswith("*"):
        return re.compile(rf"(?<!\w){re.escape(kw[:-1])}\w*")
    return re.compile(rf"(?<!\w){re.escape(kw)}(?:s|es)?(?!\w)")


_COMPILED_RULES: list[tuple[list[re.Pattern[str]], list[PlaceCategory]]] = [
    ([_keyword_pattern(k) for k in keywords], cats) for keywords, cats in _KEYWORD_RULES
]

# OSM keys whose values say what a thing *is* (names, addresses, sources are noise here).
_CLASSIFY_KEYS = frozenset({
    "historic", "military", "man_made", "natural", "railway", "building", "amenity", "tourism",
    "leisure", "industrial", "landuse", "waterway", "tunnel", "bridge", "aeroway", "power",
    "place", "castle_type", "bunker_type", "artwork_type", "tower:type", "barrier",
})
_LIFECYCLE_RE = re.compile(r"^(abandoned|disused|ruins)(:[a-z_]+)?$")

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

    for patterns, cats in _COMPILED_RULES:
        matches = sum(1 for pattern in patterns if pattern.search(text))
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
    """Build a lowercase text blob from the place's name, description and *meaningful* tags."""
    parts: list[str] = []
    if place.name:
        parts.append(place.name)
    if place.description:
        parts.append(place.description)
    for tag in place.tags:
        key, sep, value = tag.partition("=")
        if sep and key not in _CLASSIFY_KEYS and not _LIFECYCLE_RE.match(key):
            continue  # "addr:street=Park Avenue" must not make a place a park
        parts.append(f"{key} {value}" if sep else tag)
    if "osm_tags" in place.metadata:
        for k, v in place.metadata["osm_tags"].items():
            if k in _CLASSIFY_KEYS or _LIFECYCLE_RE.match(k):
                parts.append(f"{k} {v}")
    return " ".join(parts).lower()
