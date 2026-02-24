"""Story 1.1 — OSM/Overpass Integration.

Queries the Overpass API for unusual and interesting places,
normalises them into the unified Place model.
"""

from __future__ import annotations

import hashlib
import logging

import httpx

from app.config import settings
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.cache import disk_cache
from app.sources.base import BaseSource
from app.utils.geo import bounding_box
from app.utils.http_client import get_http_client
from app.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Overpass QL tag → PlaceCategory mapping
_TAG_MAP: dict[str, list[PlaceCategory]] = {
    # Unusual / exploration
    "abandoned:yes": [PlaceCategory.ABANDONED],
    "disused:yes": [PlaceCategory.ABANDONED],
    "ruins:yes": [PlaceCategory.RUINS],
    "historic:ruins": [PlaceCategory.RUINS],
    "historic:archaeological_site": [PlaceCategory.RUINS],
    "tunnel:yes": [PlaceCategory.UNDERGROUND],
    "man_made:tunnel": [PlaceCategory.UNDERGROUND],
    "natural:cave_entrance": [PlaceCategory.CAVE, PlaceCategory.UNDERGROUND],
    "military:bunker": [PlaceCategory.MILITARY, PlaceCategory.UNDERGROUND],
    "historic:bunker": [PlaceCategory.MILITARY],
    "building:bunker": [PlaceCategory.MILITARY],
    "man_made:tower": [PlaceCategory.VIEWPOINT],
    "tourism:viewpoint": [PlaceCategory.VIEWPOINT],
    "building:industrial": [PlaceCategory.INDUSTRIAL],
    "man_made:chimney": [PlaceCategory.INDUSTRIAL],
    "man_made:mineshaft": [PlaceCategory.UNDERGROUND, PlaceCategory.INDUSTRIAL],
    "tourism:artwork": [PlaceCategory.STREET_ART],
    "railway:abandoned": [PlaceCategory.TRANSPORT, PlaceCategory.ABANDONED],
    "railway:disused": [PlaceCategory.TRANSPORT, PlaceCategory.ABANDONED],
    "waterway:dam": [PlaceCategory.WATER],
    "natural:spring": [PlaceCategory.WATER, PlaceCategory.NATURE_HIDDEN],
    "natural:waterfall": [PlaceCategory.WATER, PlaceCategory.NATURE_HIDDEN],
    "natural:peak": [PlaceCategory.VIEWPOINT, PlaceCategory.NATURE_HIDDEN],
    "natural:cliff": [PlaceCategory.NATURE_HIDDEN, PlaceCategory.VIEWPOINT],
    "leisure:nature_reserve": [PlaceCategory.PARK, PlaceCategory.NATURE_HIDDEN],

    # Classic landmarks
    "historic:castle": [PlaceCategory.LANDMARK, PlaceCategory.ARCHITECTURE],
    "historic:fort": [PlaceCategory.LANDMARK, PlaceCategory.MILITARY],
    "historic:monument": [PlaceCategory.MONUMENT],
    "historic:memorial": [PlaceCategory.MONUMENT],
    "amenity:place_of_worship": [PlaceCategory.RELIGIOUS],
    "building:cathedral": [PlaceCategory.RELIGIOUS, PlaceCategory.ARCHITECTURE],
    "building:church": [PlaceCategory.RELIGIOUS],
    "building:mosque": [PlaceCategory.RELIGIOUS],
    "tourism:museum": [PlaceCategory.MUSEUM],
    "leisure:park": [PlaceCategory.PARK],
    "leisure:garden": [PlaceCategory.PARK],
    "tourism:attraction": [PlaceCategory.LANDMARK],

    # Notable restaurants/hotels (only with heritage, wikipedia, or historic tags)
    "amenity:restaurant": [PlaceCategory.RESTAURANT_NOTABLE],
    "tourism:hotel": [PlaceCategory.HOTEL_NOTABLE],
}

# Overpass query that covers both unusual and landmark objects
_OVERPASS_QUERY_TEMPLATE = """
[out:json][timeout:{timeout}];
(
  // Unusual places
  nwr["abandoned"="yes"]({bbox});
  nwr["disused"="yes"]({bbox});
  nwr["ruins"="yes"]({bbox});
  nwr["historic"="ruins"]({bbox});
  nwr["historic"="archaeological_site"]({bbox});
  nwr["tunnel"="yes"]({bbox});
  nwr["man_made"="tunnel"]({bbox});
  nwr["natural"="cave_entrance"]({bbox});
  nwr["military"="bunker"]({bbox});
  nwr["historic"="bunker"]({bbox});
  nwr["building"="bunker"]({bbox});
  nwr["man_made"="tower"]({bbox});
  nwr["building"="industrial"]["disused"="yes"]({bbox});
  nwr["man_made"="chimney"]({bbox});
  nwr["man_made"="mineshaft"]({bbox});
  nwr["tourism"="artwork"]({bbox});
  nwr["railway"="abandoned"]({bbox});
  nwr["railway"="disused"]({bbox});
  nwr["waterway"="dam"]({bbox});
  nwr["natural"="spring"]({bbox});
  nwr["natural"="waterfall"]({bbox});
  nwr["natural"="peak"]({bbox});
  nwr["leisure"="nature_reserve"]({bbox});
  nwr["natural"="cliff"]({bbox});
  // Classic landmarks
  nwr["historic"="castle"]({bbox});
  nwr["historic"="fort"]({bbox});
  nwr["historic"="monument"]({bbox});
  nwr["historic"="memorial"]({bbox});
  nwr["tourism"="museum"]({bbox});
  nwr["tourism"="viewpoint"]({bbox});
  nwr["tourism"="attraction"]({bbox});
  nwr["building"="cathedral"]({bbox});
  // Notable restaurants & hotels (only with wikipedia/heritage — truly outstanding)
  nwr["amenity"="restaurant"]["wikipedia"]({bbox});
  nwr["amenity"="restaurant"]["heritage"]({bbox});
  nwr["amenity"="restaurant"]["historic"]({bbox});
  nwr["tourism"="hotel"]["wikipedia"]({bbox});
  nwr["tourism"="hotel"]["heritage"]({bbox});
  nwr["tourism"="hotel"]["historic"]({bbox});
  nwr["tourism"="hotel"]["stars"~"^[4-5]$"]({bbox});
);
out center body;
"""


class OSMSource(BaseSource):
    """Fetches unusual and landmark places from OpenStreetMap via Overpass API."""

    source_name = "osm"

    def __init__(self) -> None:
        self._rate_limiter = RateLimiter(
            max_requests=settings.overpass_rate_limit_requests,
            window_seconds=settings.overpass_rate_limit_window_seconds,
        )

    async def search(self, lat: float, lng: float, radius_km: float) -> list[Place]:
        south, west, north, east = bounding_box(lat, lng, radius_km)
        bbox = f"{south},{west},{north},{east}"
        cache_key = f"osm_{bbox}"

        cached = await disk_cache.get(cache_key)
        if cached is not None:
            logger.debug("OSM cache hit for %s", cache_key)
            return [Place.model_validate(p) for p in cached]

        query = _OVERPASS_QUERY_TEMPLATE.format(
            timeout=settings.overpass_timeout, bbox=bbox
        )

        await self._rate_limiter.acquire()
        client = get_http_client()
        resp = await client.post(
            settings.overpass_url,
            data={"data": query},
            timeout=settings.overpass_timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        places = self._parse_elements(data.get("elements", []))
        await disk_cache.set(cache_key, [p.model_dump(mode="json") for p in places])
        logger.info("OSM returned %d places for bbox=%s", len(places), bbox)
        return places

    # ------------------------------------------------------------------

    def _parse_elements(self, elements: list[dict]) -> list[Place]:
        places: list[Place] = []
        for el in elements:
            lat, lng = self._extract_coords(el)
            if lat is None:
                continue
            tags = el.get("tags", {})
            categories = self._classify(tags)
            osm_id = f"osm_{el.get('type', 'n')}_{el['id']}"
            places.append(
                Place(
                    id=osm_id,
                    source=PlaceSource.OSM,
                    sources=[PlaceSource.OSM],
                    name=tags.get("name") or tags.get("name:en"),
                    description=tags.get("description") or tags.get("note"),
                    categories=categories,
                    coordinates=Coordinates(lat=lat, lng=lng),
                    confidence=0.7 if categories else 0.4,
                    tags=self._extract_tag_list(tags),
                    metadata={"osm_type": el.get("type"), "osm_tags": tags},
                )
            )
        return places

    @staticmethod
    def _extract_coords(el: dict) -> tuple[float | None, float | None]:
        if "lat" in el and "lon" in el:
            return el["lat"], el["lon"]
        center = el.get("center")
        if center:
            return center.get("lat"), center.get("lon")
        return None, None

    @staticmethod
    def _classify(tags: dict) -> list[PlaceCategory]:
        cats: set[PlaceCategory] = set()
        for tag_key, cat_list in _TAG_MAP.items():
            k, v = tag_key.split(":", 1)
            if tags.get(k) == v:
                cats.update(cat_list)
        # Additional heuristic: building=industrial + disused=yes
        if tags.get("building") == "industrial" and tags.get("disused") == "yes":
            cats.update([PlaceCategory.INDUSTRIAL, PlaceCategory.ABANDONED])
        # Only keep restaurant/hotel categories if place has notable markers
        notable_markers = {"wikipedia", "heritage", "historic", "wikidata", "stars"}
        if PlaceCategory.RESTAURANT_NOTABLE in cats or PlaceCategory.HOTEL_NOTABLE in cats:
            has_notable = any(k in tags for k in notable_markers)
            if not has_notable:
                cats.discard(PlaceCategory.RESTAURANT_NOTABLE)
                cats.discard(PlaceCategory.HOTEL_NOTABLE)
        return sorted(cats, key=lambda c: c.value)

    @staticmethod
    def _extract_tag_list(tags: dict) -> list[str]:
        """Extract meaningful OSM tags as a flat list of strings."""
        skip = {"source", "created_by", "building", "type"}
        return [f"{k}={v}" for k, v in tags.items() if k not in skip and len(v) < 100]
