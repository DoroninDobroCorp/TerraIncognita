"""Deep Research enrichment source.

Queries Parallel AI (OpenAI Deep Research) and Google Gemini Deep Research
to discover POIs that structured sources (OSM, Atlas Obscura, Wikidata) miss:
underwater sites, urbex, culinary specialties, modern art, cultural context.

Results are cached aggressively (30 days) since research is slow and expensive.
On first request the search is fire-and-forget: returns empty if no cache,
triggers background research. On second request the cached results are available.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any

import httpx

from app.config import settings
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.cache import DiskCache
from app.sources.base import BaseSource
from app.utils.http_client import get_http_client
from app.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_MAX_RESEARCH_TEXT_CHARS = 150_000
_MAX_JSON_ATTEMPTS = 300
_MAX_PROSE_LINES = 5_000
_MAX_PROSE_LINE_CHARS = 500
_ARRAY_OF_OBJECTS = re.compile(r"\[\s*\{")
_PROSE_NAME = re.compile(r"(?:\*\*|#{1,6}[ \t]*)([^*#\n]+)")
_PROSE_COORDS = re.compile(r"(\d{1,2}\.\d{3,6})[,\s]+(\d{1,3}\.\d{3,6})")
_json_decoder = json.JSONDecoder()


def _decode_json_at(text: str, pos: int) -> tuple[Any, int] | None:
    """The JSON value starting at ``pos`` and where it ends, or None if there is none."""
    try:
        return _json_decoder.raw_decode(text, pos)
    except (ValueError, RecursionError):
        return None


# Long-TTL cache for deep research results
_dr_cache = DiskCache(
    cache_dir=settings.cache_dir,
    ttl=settings.deep_research_cache_ttl_seconds,
)

# Runs in progress, by cache key. The map fires several discovery requests while one run takes
# minutes; without this each of them started (and paid for) its own identical run.
_running: dict[str, asyncio.Task] = {}
# "Researched, found nothing (or failed)" is remembered briefly so an empty/failed run is
# retried later instead of cached as the answer for 30 days — or re-run on every request.
# Its own directory: the keys are identical to ``_dr_cache``'s, so sharing one would make an
# empty marker look like a (30-day) cached answer.
_geocode_cache = DiskCache(cache_dir=f"{settings.cache_dir}/geocode", ttl=0)  # reverse geocoding: forever
_nominatim_limiter = RateLimiter(max_requests=1, window_seconds=1.1)  # Nominatim usage policy
_nothing_found = DiskCache(cache_dir=f"{settings.cache_dir}/dr_empty", ttl=1800)


def _configured() -> bool:
    """Deep research needs the switch on and at least one provider key."""
    return settings.deep_research_enabled and bool(
        settings.parallel_api_key or settings.gemini_api_key
    )


# Category keyword mapping for parsing research output. Keywords match whole words (plural
# allowed); a trailing "*" makes a stem. Raw substring matching put every "…street…" in
# NATURE_HIDDEN ("tree") and every "comfort" in MILITARY ("fort").
_CATEGORY_KEYWORDS: dict[str, PlaceCategory] = {
    "abandon*": PlaceCategory.ABANDONED,
    "ruin*": PlaceCategory.RUINS,
    "underground": PlaceCategory.UNDERGROUND,
    "tunnel*": PlaceCategory.UNDERGROUND,
    "bunker*": PlaceCategory.MILITARY,
    "military": PlaceCategory.MILITARY,
    "fort*": PlaceCategory.MILITARY,
    "industrial": PlaceCategory.INDUSTRIAL,
    "factor*": PlaceCategory.INDUSTRIAL,
    "cave": PlaceCategory.CAVE,
    "viewpoint": PlaceCategory.VIEWPOINT,
    "panorama": PlaceCategory.VIEWPOINT,
    "street_art": PlaceCategory.STREET_ART,
    "graffiti": PlaceCategory.STREET_ART,
    "sculpture": PlaceCategory.STREET_ART,
    "monument": PlaceCategory.MONUMENT,
    "memorial": PlaceCategory.MONUMENT,
    "statue": PlaceCategory.MONUMENT,
    "church": PlaceCategory.RELIGIOUS,
    "mosque": PlaceCategory.RELIGIOUS,
    "monaster*": PlaceCategory.RELIGIOUS,
    "abbey": PlaceCategory.RELIGIOUS,
    "museum": PlaceCategory.MUSEUM,
    "palace": PlaceCategory.ARCHITECTURE,
    "architecture": PlaceCategory.ARCHITECTURE,
    "brutalist": PlaceCategory.ARCHITECTURE,
    "castle*": PlaceCategory.LANDMARK,
    "fortress": PlaceCategory.LANDMARK,
    "landmark": PlaceCategory.LANDMARK,
    "park": PlaceCategory.PARK,
    "garden": PlaceCategory.PARK,
    "waterfall": PlaceCategory.WATER,
    "spring": PlaceCategory.WATER,
    "bay": PlaceCategory.WATER,
    "underwater": PlaceCategory.UNDERWATER,
    "shipwreck": PlaceCategory.UNDERWATER,
    "diving": PlaceCategory.UNDERWATER,
    "snorkeling": PlaceCategory.UNDERWATER,
    "amphora": PlaceCategory.UNDERWATER,
    "culinary": PlaceCategory.CULINARY,
    "restaurant": PlaceCategory.CULINARY,
    "food": PlaceCategory.CULINARY,
    "wine": PlaceCategory.CULINARY,
    "beer": PlaceCategory.CULINARY,
    "dish": PlaceCategory.CULINARY,
    "cuisine": PlaceCategory.CULINARY,
    "tasting": PlaceCategory.CULINARY,
    "nature": PlaceCategory.NATURE_HIDDEN,
    "canyon": PlaceCategory.NATURE_HIDDEN,
    "hiking": PlaceCategory.NATURE_HIDDEN,
    "olive": PlaceCategory.NATURE_HIDDEN,
    "tree": PlaceCategory.NATURE_HIDDEN,
    "railway": PlaceCategory.TRANSPORT,
    "train": PlaceCategory.TRANSPORT,
    "aqueduct*": PlaceCategory.ARCHITECTURE,
}

_CATEGORY_PATTERNS: list[tuple[re.Pattern[str], PlaceCategory]] = [
    (
        re.compile(
            rf"(?<!\w){re.escape(keyword[:-1])}\w*"
            if keyword.endswith("*")
            else rf"(?<!\w){re.escape(keyword)}(?:s|es)?(?!\w)"
        ),
        category,
    )
    for keyword, category in _CATEGORY_KEYWORDS.items()
]

# Structured prompt that asks for JSON POI output
_RESEARCH_PROMPT = """Research unusual, hidden, and extraordinary points of interest within {radius_km} km
of the city center of {city_name} ({country}), coordinates: {lat}, {lng}.

Focus on objects that typical tourist guides and OpenStreetMap miss:
1. UNDERWATER: shipwrecks, underwater caves, archaeological sites on the seabed
2. URBEX: abandoned factories, military bunkers, cold war tunnels, ghost buildings
3. CULINARY: unique local dishes, unusual drinks, rare food specialties, notable restaurants
4. MODERN ART: recent sculptures, street art, brutalist architecture, unusual monuments
5. HIDDEN NATURE: ancient trees, secret viewpoints, canyons, hidden springs
6. CULTURAL ANOMALIES: places with strange history, religious paradoxes, legends

For EACH point of interest, provide:
- name (local name + English translation)
- GPS coordinates (latitude, longitude) — CRITICAL, must be accurate
- brief description (2-3 sentences)
- category (one of: underwater, abandoned, ruins, military, industrial, cave, culinary,
  architecture, monument, street_art, nature_hidden, viewpoint, water, religious, landmark, museum)
- source_url (web link where you found this information)
- access_info (how to get there, entry fee, restrictions)
- safety_notes (any warnings)

IMPORTANT:
- Verify GPS coordinates are actually within {radius_km} km of ({lat}, {lng})
- Include coordinates for ALL items, even restaurants and food spots
- Prefer lesser-known places over famous tourist attractions
- Include at least 3-5 culinary items with restaurant names and coordinates

Format your response as JSON array:
[
  {{
    "name": "Place Name",
    "name_local": "Местное название",
    "lat": 42.0934,
    "lng": 19.1005,
    "description": "Brief description",
    "category": "category_name",
    "source_url": "https://...",
    "access_info": "How to access",
    "safety_notes": "Any warnings"
  }}
]

Return ONLY the JSON array, no other text."""


class DeepResearchSource(BaseSource):
    """Discovers POIs via Parallel AI and Gemini Deep Research APIs."""

    source_name = "deep_research"

    async def get_status(self, lat: float, lng: float, radius_km: float) -> tuple[str, str | None]:
        """Returns (status, message) tuple.

        Status: "idle" | "pending" | "cached"
        """
        if not _configured():
            return ("idle", None)

        cache_key = f"dr_{lat:.2f}_{lng:.2f}_{radius_km:.0f}"
        cached = await _dr_cache.get(cache_key) or None  # [] = an empty entry written by the old 30-day cache

        if cached is not None:
            count = len(cached)
            return ("cached", f"{count} additional places found via Deep Research")

        if cache_key in _running:
            return (
                "pending",
                "Deep Research in progress... Check back in 2-5 minutes for additional hidden gems!",
            )
        return ("idle", None)

    async def search(self, lat: float, lng: float, radius_km: float) -> list[Place]:
        if not _configured():
            return []

        cache_key = f"dr_{lat:.2f}_{lng:.2f}_{radius_km:.0f}"
        cached = await _dr_cache.get(cache_key) or None  # [] = old empty entry: ask again
        if cached is not None:
            logger.debug("Deep Research cache hit for %s", cache_key)
            return [Place.model_validate(p) for p in cached]

        if cache_key in _running or await _nothing_found.get(cache_key) is not None:
            return []  # already researching / just came back empty-handed

        # Fire-and-forget: trigger research in background, return empty now
        task = asyncio.create_task(self._research_and_cache(lat, lng, radius_km, cache_key))
        _running[cache_key] = task  # also keeps a reference so the task cannot be garbage-collected
        task.add_done_callback(lambda _t, key=cache_key: _running.pop(key, None))
        logger.info("Deep Research triggered in background for %s (no cache yet)", cache_key)
        return []

    async def search_sync(self, lat: float, lng: float, radius_km: float) -> list[Place]:
        """Blocking version: waits for research to complete. Use for prefetch/CLI."""
        if not _configured():
            return []

        cache_key = f"dr_{lat:.2f}_{lng:.2f}_{radius_km:.0f}"
        cached = await _dr_cache.get(cache_key) or None  # [] = old empty entry: ask again
        if cached is not None:
            return [Place.model_validate(p) for p in cached]

        return await self._research_and_cache(lat, lng, radius_km, cache_key)

    async def _research_and_cache(
        self, lat: float, lng: float, radius_km: float, cache_key: str
    ) -> list[Place]:
        """Run both research APIs, merge results, cache them."""
        # Two geocoding requests, one after the other (Nominatim's policy: at most 1 request/s), shared
        # by both providers' prompts.
        city_name = await self._reverse_geocode(lat, lng)
        country = await self._guess_country(lat, lng)

        parallel_task = asyncio.create_task(
            self._research_parallel(lat, lng, radius_km, city_name, country)
        )
        gemini_task = asyncio.create_task(
            self._research_gemini(lat, lng, radius_km, city_name, country)
        )

        results = await asyncio.gather(parallel_task, gemini_task, return_exceptions=True)

        all_places: list[Place] = []
        for i, result in enumerate(results):
            source = ["Parallel AI", "Gemini"][i]
            if isinstance(result, Exception):
                logger.warning("Deep Research %s failed: %s", source, result)
            elif isinstance(result, list):
                logger.info("Deep Research %s returned %d places", source, len(result))
                all_places.extend(result)

        # Deduplicate by proximity (100m threshold for research results)
        merged = self._deduplicate(all_places, threshold_m=100)

        if merged:
            await _dr_cache.set(cache_key, [p.model_dump(mode="json") for p in merged])
            logger.info("Deep Research cached %d merged places for %s", len(merged), cache_key)
        else:
            # Empty usually means both providers failed or timed out: do not pin that for 30 days.
            await _nothing_found.set(cache_key, [])
            logger.warning("Deep Research found nothing for %s; will retry later", cache_key)
        return merged

    # --- Parallel AI (OpenAI Deep Research) ---

    async def _research_parallel(
        self, lat: float, lng: float, radius_km: float, city_name: str,
        country: str | None = None,
    ) -> list[Place]:
        if not settings.parallel_api_key:
            logger.debug("Parallel API key not set, skipping")
            return []

        prompt = _RESEARCH_PROMPT.format(
            city_name=city_name,
            country=country if country is not None else await self._guess_country(lat, lng),
            lat=lat, lng=lng, radius_km=radius_km,
        )

        try:
            client = get_http_client()

            # Start task
            resp = await client.post(
                settings.parallel_api_url,
                headers={
                    "x-api-key": settings.parallel_api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "processor": "base",
                    "input": prompt,
                    "task_spec": {
                        "output_schema": {
                            "type": "json",
                            "json_schema": {
                                "type": "object",
                                "properties": {
                                    "places": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "name_local": {"type": "string"},
                                                "lat": {"type": "number"},
                                                "lng": {"type": "number"},
                                                "description": {"type": "string"},
                                                "category": {"type": "string"},
                                                "source_url": {"type": "string"},
                                                "access_info": {"type": "string"},
                                                "safety_notes": {"type": "string"},
                                            },
                                            "required": [
                                                "name", "lat", "lng", "description",
                                            ],
                                        },
                                    }
                                },
                                "required": ["places"],
                            },
                        }
                    },
                },
                timeout=60,
            )
            resp.raise_for_status()
            run_data = resp.json()
            run_id = run_data["run_id"]
            logger.info("Parallel AI research started: %s", run_id)

            # Poll for completion
            places = await self._poll_parallel(client, run_id)
            return places

        except Exception as exc:
            logger.warning("Parallel AI research failed: %s", exc)
            return []

    async def _poll_parallel(self, client: httpx.AsyncClient, run_id: str) -> list[Place]:
        """Poll Parallel AI for task completion."""
        url = f"{settings.parallel_api_url}/{run_id}/result"
        elapsed = 0

        while elapsed < settings.deep_research_max_wait:
            await asyncio.sleep(settings.deep_research_poll_interval)
            elapsed += settings.deep_research_poll_interval

            try:
                resp = await client.get(
                    url,
                    headers={"x-api-key": settings.parallel_api_key},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                run_status = data.get("run", {}).get("status", "")
                if run_status == "completed":
                    output = data.get("output", {})
                    content = output.get("content")
                    if content is None:
                        logger.warning("Parallel AI completed but no content")
                        return []
                    return self._parse_research_json(content, "parallel")
                elif run_status == "failed":
                    logger.warning("Parallel AI task failed")
                    return []

            except Exception as exc:
                logger.debug("Parallel AI poll error: %s", exc)

        logger.warning("Parallel AI timed out after %ds", elapsed)
        return []

    # --- Gemini Deep Research ---

    async def _research_gemini(
        self, lat: float, lng: float, radius_km: float, city_name: str,
        country: str | None = None,
    ) -> list[Place]:
        if not settings.gemini_api_key:
            logger.debug("Gemini API key not set, skipping")
            return []

        prompt = _RESEARCH_PROMPT.format(
            city_name=city_name,
            country=country if country is not None else await self._guess_country(lat, lng),
            lat=lat, lng=lng, radius_km=radius_km,
        )

        try:
            client = get_http_client()

            # Start Gemini Deep Research interaction
            resp = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": settings.gemini_api_key,
                },
                json={
                    "input": prompt,
                    "agent": "deep-research-pro-preview-12-2025",
                    "background": True,
                },
                timeout=60,
            )
            resp.raise_for_status()
            interaction = resp.json()
            interaction_id = interaction.get("id") or interaction.get("name", "")
            logger.info("Gemini Deep Research started: %s", interaction_id)

            # Poll for completion
            places = await self._poll_gemini(client, interaction_id)
            return places

        except Exception as exc:
            logger.warning("Gemini Deep Research failed: %s", exc)
            return []

    async def _poll_gemini(self, client: httpx.AsyncClient, interaction_id: str) -> list[Place]:
        """Poll Gemini for interaction completion."""
        url = f"https://generativelanguage.googleapis.com/v1beta/interactions/{interaction_id}"
        elapsed = 0

        while elapsed < settings.deep_research_max_wait:
            await asyncio.sleep(settings.deep_research_poll_interval)
            elapsed += settings.deep_research_poll_interval

            try:
                resp = await client.get(
                    url,
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                status = data.get("status", "")
                if status == "completed":
                    outputs = data.get("outputs", [])
                    if outputs:
                        text = outputs[-1].get("text", "")
                        return self._parse_research_text(text, "gemini")
                    return []
                elif status == "failed":
                    logger.warning("Gemini research failed: %s", data.get("error"))
                    return []

            except Exception as exc:
                logger.debug("Gemini poll error: %s", exc)

        logger.warning("Gemini research timed out after %ds", elapsed)
        return []

    # --- Parsing ---

    def _parse_research_json(self, content: Any, provider: str) -> list[Place]:
        """Parse structured JSON output (from Parallel AI)."""
        items = []
        if isinstance(content, list):
            items = content
        elif isinstance(content, dict):
            # Try common wrapper keys
            for key in ("places", "pois", "results", "items"):
                if key in content and isinstance(content[key], list):
                    items = content[key]
                    break
            if not items:
                items = [content]
        elif isinstance(content, str):
            return self._parse_research_text(content, provider)

        return self._items_to_places(items, provider)

    def _parse_research_text(self, text: str, provider: str) -> list[Place]:
        """Parse unstructured text: a JSON array of places, loose JSON objects, or markdown prose.

        The text comes from an LLM that browsed the web, so it is bounded and scanned with a real JSON
        decoder at a limited number of candidate positions (nested lazy regexes took seconds of CPU,
        on the event loop, for a hundred kilobytes of the wrong kind of text).
        """
        text = text[:_MAX_RESEARCH_TEXT_CHARS]

        for attempt, match in enumerate(_ARRAY_OF_OBJECTS.finditer(text)):
            if attempt >= _MAX_JSON_ATTEMPTS:
                break
            decoded = _decode_json_at(text, match.start())
            if decoded is not None and isinstance(decoded[0], list):
                places = self._items_to_places([i for i in decoded[0] if isinstance(i, dict)], provider)
                if places:
                    return places

        places = []
        pos = attempts = 0
        while attempts < _MAX_JSON_ATTEMPTS:
            start = text.find("{", pos)
            if start < 0:
                break
            attempts += 1
            decoded = _decode_json_at(text, start)
            place = self._item_to_place(decoded[0], provider) if decoded and isinstance(decoded[0], dict) else None
            if place:
                places.append(place)
                pos = decoded[1]  # a place: do not look inside it again
            else:
                pos = start + 1  # a wrapper object, or not JSON: look at what is inside
        if places:
            return places

        # Fallback: try to extract POIs from markdown/text using patterns
        return self._extract_from_prose(text, provider)

    def _extract_from_prose(self, text: str, provider: str) -> list[Place]:
        """Last resort: markdown lines such as ``**Fort Mamula** - 42.4381, 18.5694`` or
        ``### Hidden Cave (42.5, 18.6123)``: a bold or heading name, then coordinates, on one line."""
        places: dict[str, Place] = {}
        for line in text.splitlines()[:_MAX_PROSE_LINES]:
            line = line[:_MAX_PROSE_LINE_CHARS]
            name_match = _PROSE_NAME.search(line)
            if not name_match:
                continue
            coords = _PROSE_COORDS.search(line, name_match.start(1))
            if not coords:
                continue
            name = line[name_match.start(1):min(name_match.end(1), coords.start())]
            name = name.strip(" \t-\u2013\u2014:,([")
            if len(name) < 2 or len(name) > 120:
                continue
            lat, lng = float(coords.group(1)), float(coords.group(2))
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                continue
            place_id = f"dr_{provider}_{hashlib.md5(name.encode()).hexdigest()[:8]}"
            places.setdefault(place_id, Place(
                id=place_id,
                source=PlaceSource.DEEP_RESEARCH,
                sources=[PlaceSource.DEEP_RESEARCH],
                name=name,
                categories=self._guess_categories(name, "") or [PlaceCategory.LANDMARK],
                coordinates=Coordinates(lat=lat, lng=lng),
                confidence=0.6,
                metadata={"research_provider": provider},
            ))
        return list(places.values())

    def _items_to_places(self, items: list[dict], provider: str) -> list[Place]:
        """Convert a list of raw dicts to Place objects."""
        places: list[Place] = []
        for item in items:
            p = self._item_to_place(item, provider)
            if p:
                places.append(p)
        return places

    def _item_to_place(self, item: dict, provider: str) -> Place | None:
        """Convert a single research result dict to a Place."""
        try:
            name = item.get("name") or item.get("title") or ""
            if not name:
                return None

            lat = float(item.get("lat", 0))
            lng = float(item.get("lng") or item.get("lon", 0))
            if lat == 0 and lng == 0:
                return None
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                return None

            desc = item.get("description") or item.get("brief_description") or ""
            category_str = item.get("category", "")
            source_url = item.get("source_url") or item.get("url") or ""
            access_info = item.get("access_info") or ""
            safety = item.get("safety_notes") or ""
            name_local = item.get("name_local") or ""

            categories = self._guess_categories(name + " " + desc, category_str)

            place_id = f"dr_{provider}_{hashlib.md5(name.encode()).hexdigest()[:8]}"

            metadata: dict[str, Any] = {"research_provider": provider}
            if source_url:
                metadata["source_url"] = source_url
            if access_info:
                metadata["access_info"] = access_info
            if safety:
                metadata["safety_notes"] = safety
            if name_local:
                metadata["name_local"] = name_local

            full_desc = desc
            if access_info:
                full_desc += f"\n\n📍 {access_info}"
            if safety:
                full_desc += f"\n⚠️ {safety}"

            return Place(
                id=place_id,
                source=PlaceSource.DEEP_RESEARCH,
                sources=[PlaceSource.DEEP_RESEARCH],
                name=name,
                description=full_desc if full_desc else None,
                categories=categories or [PlaceCategory.LANDMARK],
                coordinates=Coordinates(lat=lat, lng=lng),
                confidence=0.75,
                category_confidence={c.value: 0.7 for c in categories} if categories else {},
                tags=[f"deep_research:{provider}"],
                metadata=metadata,
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("Skipping research item: %s", exc)
            return None

    def _guess_categories(self, text: str, category_hint: str) -> list[PlaceCategory]:
        """Infer categories from text content and explicit category hint."""
        cats: set[PlaceCategory] = set()
        combined = (text + " " + category_hint).lower()

        # Check explicit category hint first
        if category_hint:
            hint = category_hint.strip().lower().replace(" ", "_")
            try:
                cats.add(PlaceCategory(hint))
            except ValueError:
                pass

        # Keyword matching
        for pattern, cat in _CATEGORY_PATTERNS:
            if pattern.search(combined):
                cats.add(cat)

        return sorted(cats, key=lambda c: c.value)

    def _deduplicate(self, places: list[Place], threshold_m: float = 100) -> list[Place]:
        """Simple deduplication by name similarity and geo-proximity."""
        if not places:
            return []

        from app.utils.geo import haversine_distance_m

        merged: list[Place] = []
        used: set[int] = set()

        for i, p in enumerate(places):
            if i in used:
                continue
            best = p
            for j in range(i + 1, len(places)):
                if j in used:
                    continue
                dist = haversine_distance_m(
                    p.coordinates.lat, p.coordinates.lng,
                    places[j].coordinates.lat, places[j].coordinates.lng,
                )
                # Same place if <100m AND similar name
                name_sim = self._name_similarity(p.name or "", places[j].name or "")
                if dist < threshold_m and name_sim > 0.3:
                    used.add(j)
                    # Keep the one with more data
                    if len(places[j].description or "") > len(best.description or ""):
                        best = places[j]
                        # Merge metadata
                        best.metadata.update(p.metadata)

            merged.append(best)
            used.add(i)

        return merged

    @staticmethod
    def _name_similarity(a: str, b: str) -> float:
        """Simple word overlap similarity."""
        if not a or not b:
            return 0.0
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        overlap = len(words_a & words_b)
        return overlap / min(len(words_a), len(words_b))

    # --- Geocoding helpers ---

    async def _nominatim(self, lat: float, lng: float, zoom: int) -> dict:
        """One Nominatim reverse lookup: cached for good (a place does not move) and rate limited."""
        key = f"{lat:.3f}_{lng:.3f}_{zoom}"
        cached = await _geocode_cache.get(key)
        if isinstance(cached, dict):
            return cached
        await _nominatim_limiter.acquire()
        resp = await get_http_client().get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "lat": str(lat), "lon": str(lng), "format": "json", "zoom": str(zoom),
                "accept-language": "en",
            },
            headers={"User-Agent": settings.wikimedia_user_agent},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            await _geocode_cache.set(key, data)
            return data
        return {}

    async def _reverse_geocode(self, lat: float, lng: float) -> str:
        """Get city name from coordinates using Nominatim."""
        try:
            data = await self._nominatim(lat, lng, 10)
            address = data.get("address", {})
            return (
                address.get("city")
                or address.get("town")
                or address.get("village")
                or address.get("municipality")
                or data.get("display_name", "Unknown").split(",")[0]
            )
        except Exception:
            return "Unknown"

    async def _guess_country(self, lat: float, lng: float) -> str:
        """Best-effort country detection from coordinates."""
        try:
            data = await self._nominatim(lat, lng, 3)
            return data.get("address", {}).get("country", "")
        except Exception:
            return ""
