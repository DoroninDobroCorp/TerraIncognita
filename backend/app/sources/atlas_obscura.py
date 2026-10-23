"""Story 1.2 — Atlas Obscura Integration.

Fetches curated unusual places from Atlas Obscura via web scraping.
Maps their categories to PlaceCategory and deduplicates with other sources.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time

import httpx

from app.config import settings
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.cache import disk_cache
from app.sources.base import BaseSource
from app.utils.geo import bounding_box
from app.utils.http_client import get_http_client

logger = logging.getLogger(__name__)

# Atlas Obscura category → PlaceCategory mapping
_CATEGORY_MAP: dict[str, list[PlaceCategory]] = {
    "abandoned": [PlaceCategory.ABANDONED],
    "ruins": [PlaceCategory.RUINS],
    "tunnels": [PlaceCategory.UNDERGROUND],
    "caves": [PlaceCategory.CAVE, PlaceCategory.UNDERGROUND],
    "bunkers": [PlaceCategory.MILITARY, PlaceCategory.UNDERGROUND],
    "military": [PlaceCategory.MILITARY],
    "industrial": [PlaceCategory.INDUSTRIAL],
    "street art": [PlaceCategory.STREET_ART],
    "natural wonders": [PlaceCategory.NATURE_HIDDEN],
    "waterfalls": [PlaceCategory.WATER, PlaceCategory.NATURE_HIDDEN],
    "springs": [PlaceCategory.WATER],
    "viewpoints": [PlaceCategory.VIEWPOINT],
    "architecture": [PlaceCategory.ARCHITECTURE],
    "museums": [PlaceCategory.MUSEUM],
    "monuments & memorials": [PlaceCategory.MONUMENT],
    "castles": [PlaceCategory.LANDMARK, PlaceCategory.ARCHITECTURE],
    "religious sites": [PlaceCategory.RELIGIOUS],
    "parks": [PlaceCategory.PARK],
    "churches & cathedrals": [PlaceCategory.RELIGIOUS, PlaceCategory.ARCHITECTURE],
    "forts": [PlaceCategory.MILITARY, PlaceCategory.LANDMARK],
}


class AtlasObscuraError(RuntimeError):
    """Atlas Obscura could not be queried (blocked, down, or a back-off is active)."""


# After a failure (it is behind bot protection and answers 403 to API clients) stay away this long
# instead of spending two doomed requests on every discovery.
_BACKOFF_SECONDS = 600.0


class AtlasObscuraSource(BaseSource):
    """Discovers unusual places from Atlas Obscura's public pages."""

    source_name = "atlas"

    def __init__(self) -> None:
        self._retry_after = 0.0  # time.monotonic() before which we do not even try

    async def search(self, lat: float, lng: float, radius_km: float) -> list[Place]:
        cache_key = f"atlas_{lat:.3f}_{lng:.3f}_{radius_km:.1f}"
        cached = await disk_cache.get(cache_key)
        if cached is not None:
            logger.debug("Atlas Obscura cache hit")
            return [Place.model_validate(p) for p in cached]

        if time.monotonic() < self._retry_after:
            raise AtlasObscuraError("backing off after a recent failure")
        try:
            places = await self._fetch_nearby(lat, lng, radius_km)
        except AtlasObscuraError:
            self._retry_after = time.monotonic() + _BACKOFF_SECONDS
            raise
        # Only a real answer is cached: a failure must not look like "nothing nearby" for 24 h.
        await disk_cache.set(cache_key, [p.model_dump(mode="json") for p in places])
        logger.info("Atlas Obscura returned %d places", len(places))
        return places

    async def _fetch_nearby(self, lat: float, lng: float, radius_km: float) -> list[Place]:
        """Fetch places from Atlas Obscura's nearby endpoint."""
        url = f"{settings.atlas_obscura_base_url}/api/v2/search/places"
        params = {
            "lat": str(lat),
            "lng": str(lng),
            "radius": str(radius_km * 1000),  # meters
            "limit": "50",
        }
        headers = {
            "User-Agent": settings.wikimedia_user_agent,
            "Accept": "application/json",
        }

        try:
            client = get_http_client()
            resp = await client.get(
                url, params=params, headers=headers,
                timeout=settings.atlas_obscura_timeout,
            )
            if resp.status_code == 403:
                logger.warning("Atlas Obscura returned 403 — trying HTML scrape fallback")
                return await self._scrape_fallback(lat, lng, radius_km, client)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AtlasObscuraError(f"Atlas Obscura unavailable: {exc}") from exc

        return self._parse_api_response(data)

    async def _scrape_fallback(
        self, lat: float, lng: float, radius_km: float, client: httpx.AsyncClient
    ) -> list[Place]:
        """Fallback: scrape the HTML search page when the JSON API is blocked."""
        south, west, north, east = bounding_box(lat, lng, radius_km)
        url = (
            f"{settings.atlas_obscura_base_url}/search"
            f"?lat={lat}&lng={lng}&nearby=1"
        )
        headers = {
            "User-Agent": settings.wikimedia_user_agent,
            "Accept": "text/html",
        }
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return self._parse_html(resp.text, south, west, north, east)
        except httpx.HTTPError as exc:
            raise AtlasObscuraError(f"Atlas Obscura HTML fallback failed: {exc}") from exc

    def _parse_api_response(self, data: dict) -> list[Place]:
        places: list[Place] = []
        items = data if isinstance(data, list) else data.get("places", data.get("results", []))
        for item in items:
            try:
                coords = Coordinates(
                    lat=float(item["lat"]),
                    lng=float(item["lng"]),
                )
                categories = self._map_categories(
                    item.get("categories", []),
                    item.get("tags", []),
                )
                places.append(
                    Place(
                        id=f"atlas_{item.get('id', item.get('slug', ''))}",
                        source=PlaceSource.ATLAS_OBSCURA,
                        sources=[PlaceSource.ATLAS_OBSCURA],
                        name=item.get("title") or item.get("name"),
                        description=item.get("subtitle") or item.get("description"),
                        categories=categories or [PlaceCategory.LANDMARK],
                        coordinates=coords,
                        confidence=0.85,
                        tags=item.get("tags", []),
                        photos=[item["photo"]] if item.get("photo") else [],
                        metadata={
                            "atlas_url": item.get("url", ""),
                            **self._absolute_url(item.get("url")),
                        },
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("Skipping Atlas item: %s", exc)
        return places

    def _parse_html(
        self, html: str, south: float, west: float, north: float, east: float
    ) -> list[Place]:
        """Minimal HTML parser for Atlas Obscura search results page."""
        places: list[Place] = []
        # Regex-based lightweight extraction (no BeautifulSoup dependency)
        pattern = re.compile(
            r'data-lat="([^"]+)"\s+data-lng="([^"]+)".*?'
            r'class="title[^"]*"[^>]*>([^<]+)<',
            re.DOTALL,
        )
        for m in pattern.finditer(html):
            try:
                lat, lng = float(m.group(1)), float(m.group(2))
                if not (south <= lat <= north and west <= lng <= east):
                    continue
                name = m.group(3).strip()
                places.append(
                    Place(
                        # Stable across processes (str hash() is salted per run).
                        id=f"atlas_html_{hashlib.md5(name.encode()).hexdigest()[:8]}",
                        source=PlaceSource.ATLAS_OBSCURA,
                        sources=[PlaceSource.ATLAS_OBSCURA],
                        name=name,
                        categories=[PlaceCategory.LANDMARK],
                        coordinates=Coordinates(lat=lat, lng=lng),
                        confidence=0.75,
                    )
                )
            except (ValueError, TypeError):
                continue
        return places

    @staticmethod
    def _absolute_url(url: str | None) -> dict[str, str]:
        if not url:
            return {}
        if url.startswith("/"):
            url = settings.atlas_obscura_base_url + url
        return {"source_url": url} if url.startswith("https://") else {}

    @staticmethod
    def _map_categories(
        ao_categories: list[str], ao_tags: list[str]
    ) -> list[PlaceCategory]:
        cats: set[PlaceCategory] = set()
        for cat_name in ao_categories + ao_tags:
            key = cat_name.strip().lower()
            if key in _CATEGORY_MAP:
                cats.update(_CATEGORY_MAP[key])
        return sorted(cats, key=lambda c: c.value)
