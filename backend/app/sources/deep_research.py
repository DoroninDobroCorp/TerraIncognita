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
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.cache import DiskCache
from app.sources.base import BaseSource
from app.utils.http_client import get_http_client

logger = logging.getLogger(__name__)

# Long-TTL cache for deep research results
_dr_cache = DiskCache(
    cache_dir=settings.cache_dir,
    ttl=settings.deep_research_cache_ttl_seconds,
)

# Category keyword mapping for parsing research output
_CATEGORY_KEYWORDS: dict[str, PlaceCategory] = {
    "abandoned": PlaceCategory.ABANDONED,
    "ruins": PlaceCategory.RUINS,
    "underground": PlaceCategory.UNDERGROUND,
    "tunnel": PlaceCategory.UNDERGROUND,
    "bunker": PlaceCategory.MILITARY,
    "military": PlaceCategory.MILITARY,
    "fort": PlaceCategory.MILITARY,
    "industrial": PlaceCategory.INDUSTRIAL,
    "factory": PlaceCategory.INDUSTRIAL,
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
    "monastery": PlaceCategory.RELIGIOUS,
    "abbey": PlaceCategory.RELIGIOUS,
    "museum": PlaceCategory.MUSEUM,
    "palace": PlaceCategory.ARCHITECTURE,
    "architecture": PlaceCategory.ARCHITECTURE,
    "brutalist": PlaceCategory.ARCHITECTURE,
    "castle": PlaceCategory.LANDMARK,
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
    "aqueduct": PlaceCategory.ARCHITECTURE,
}

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
        if not settings.deep_research_enabled:
            return ("idle", None)
        
        cache_key = f"dr_{lat:.2f}_{lng:.2f}_{radius_km:.0f}"
        cached = await _dr_cache.get(cache_key)
        
        if cached is not None:
            count = len(cached)
            return ("cached", f"{count} additional places found via Deep Research")
        
        # Check if research is in progress (we don't track this explicitly,
        # but we can assume it's pending on first request)
        return ("pending", "Deep Research in progress... Check back in 2-5 minutes for additional hidden gems!")

    async def search(self, lat: float, lng: float, radius_km: float) -> list[Place]:
        if not settings.deep_research_enabled:
            return []

        cache_key = f"dr_{lat:.2f}_{lng:.2f}_{radius_km:.0f}"
        cached = await _dr_cache.get(cache_key)
        if cached is not None:
            logger.debug("Deep Research cache hit for %s", cache_key)
            return [Place.model_validate(p) for p in cached]

        # Fire-and-forget: trigger research in background, return empty now
        asyncio.create_task(self._research_and_cache(lat, lng, radius_km, cache_key))
        logger.info("Deep Research triggered in background for %s (no cache yet)", cache_key)
        return []

    async def search_sync(self, lat: float, lng: float, radius_km: float) -> list[Place]:
        """Blocking version: waits for research to complete. Use for prefetch/CLI."""
        if not settings.deep_research_enabled:
            return []

        cache_key = f"dr_{lat:.2f}_{lng:.2f}_{radius_km:.0f}"
        cached = await _dr_cache.get(cache_key)
        if cached is not None:
            return [Place.model_validate(p) for p in cached]

        return await self._research_and_cache(lat, lng, radius_km, cache_key)

    async def _research_and_cache(
        self, lat: float, lng: float, radius_km: float, cache_key: str
    ) -> list[Place]:
        """Run both research APIs, merge results, cache them."""
        city_name = await self._reverse_geocode(lat, lng)

        parallel_task = asyncio.create_task(
            self._research_parallel(lat, lng, radius_km, city_name)
        )
        gemini_task = asyncio.create_task(
            self._research_gemini(lat, lng, radius_km, city_name)
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

        await _dr_cache.set(cache_key, [p.model_dump(mode="json") for p in merged])
        logger.info("Deep Research cached %d merged places for %s", len(merged), cache_key)
        return merged

    # --- Parallel AI (OpenAI Deep Research) ---

    async def _research_parallel(
        self, lat: float, lng: float, radius_km: float, city_name: str
    ) -> list[Place]:
        if not settings.parallel_api_key:
            logger.debug("Parallel API key not set, skipping")
            return []

        prompt = _RESEARCH_PROMPT.format(
            city_name=city_name,
            country=await self._guess_country(lat, lng),
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
        self, lat: float, lng: float, radius_km: float, city_name: str
    ) -> list[Place]:
        if not settings.gemini_api_key:
            logger.debug("Gemini API key not set, skipping")
            return []

        prompt = _RESEARCH_PROMPT.format(
            city_name=city_name,
            country=await self._guess_country(lat, lng),
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
        """Parse unstructured text, extract JSON arrays or structured data."""
        # Try to find JSON array in text
        json_match = re.search(r'\[[\s\S]*?\{[\s\S]*?\}[\s\S]*?\]', text)
        if json_match:
            try:
                items = json.loads(json_match.group())
                if isinstance(items, list):
                    return self._items_to_places(items, provider)
            except json.JSONDecodeError:
                pass

        # Try to find individual JSON objects
        places: list[Place] = []
        for obj_match in re.finditer(r'\{[^{}]*"name"[^{}]*"lat"[^{}]*\}', text):
            try:
                item = json.loads(obj_match.group())
                p = self._item_to_place(item, provider)
                if p:
                    places.append(p)
            except (json.JSONDecodeError, ValueError):
                continue

        if places:
            return places

        # Fallback: try to extract POIs from markdown/text using patterns
        return self._extract_from_prose(text, provider)

    def _extract_from_prose(self, text: str, provider: str) -> list[Place]:
        """Last resort: extract POIs from unstructured prose using coordinate patterns."""
        places: list[Place] = []
        # Match patterns like "42.0934, 19.1005" or "(42.0934, 19.1005)"
        coord_pattern = re.compile(
            r'(?:^|\n).*?(?:\*\*|#{1,3}\s*)'
            r'([^\n*#]+?)'  # name
            r'(?:\*\*)?.*?'
            r'(\d{1,2}\.\d{3,6})[,\s]+(\d{1,3}\.\d{3,6})',
            re.MULTILINE,
        )
        for m in coord_pattern.finditer(text):
            name = m.group(1).strip().rstrip(':').strip()
            try:
                lat = float(m.group(2))
                lng = float(m.group(3))
                if -90 <= lat <= 90 and -180 <= lng <= 180:
                    place_id = f"dr_{provider}_{hashlib.md5(name.encode()).hexdigest()[:8]}"
                    categories = self._guess_categories(name, "")
                    places.append(Place(
                        id=place_id,
                        source=PlaceSource.DEEP_RESEARCH,
                        sources=[PlaceSource.DEEP_RESEARCH],
                        name=name,
                        categories=categories or [PlaceCategory.LANDMARK],
                        coordinates=Coordinates(lat=lat, lng=lng),
                        confidence=0.6,
                        metadata={"research_provider": provider},
                    ))
            except ValueError:
                continue
        return places

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
        for keyword, cat in _CATEGORY_KEYWORDS.items():
            if keyword in combined:
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

    async def _reverse_geocode(self, lat: float, lng: float) -> str:
        """Get city name from coordinates using Nominatim."""
        try:
            client = get_http_client()
            resp = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": str(lat), "lon": str(lng),
                    "format": "json", "zoom": "10",
                    "accept-language": "en",
                },
                headers={"User-Agent": "TerraIncognita/0.1"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
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
            client = get_http_client()
            resp = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": str(lat), "lon": str(lng),
                    "format": "json", "zoom": "3",
                    "accept-language": "en",
                },
                headers={"User-Agent": "TerraIncognita/0.1"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("address", {}).get("country", "")
        except Exception:
            return ""
