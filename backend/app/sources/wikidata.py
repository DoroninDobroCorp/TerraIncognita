"""Story 1.3 — Wikidata/Wikipedia Geo-entities.

SPARQL queries to Wikidata for historical and hidden objects within a bounding box,
enriched with Wikipedia article summaries.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.cache import disk_cache
from app.sources.base import BaseSource
from app.utils.geo import bounding_box
from app.utils.http_client import get_http_client

logger = logging.getLogger(__name__)

# Wikidata type → PlaceCategory
_TYPE_MAP: dict[str, list[PlaceCategory]] = {
    "Q839954": [PlaceCategory.RUINS],                    # archaeological site
    "Q82117": [PlaceCategory.RUINS],                     # ruin
    "Q57821": [PlaceCategory.MILITARY, PlaceCategory.LANDMARK],  # fortification
    "Q23413": [PlaceCategory.LANDMARK, PlaceCategory.ARCHITECTURE],  # castle
    "Q16970": [PlaceCategory.RELIGIOUS],                  # church building
    "Q32815": [PlaceCategory.RELIGIOUS],                  # mosque
    "Q34627": [PlaceCategory.RELIGIOUS],                  # synagogue
    "Q33506": [PlaceCategory.MUSEUM],                     # museum
    "Q4989906": [PlaceCategory.MONUMENT],                 # monument
    "Q35509": [PlaceCategory.CAVE, PlaceCategory.UNDERGROUND],  # cave
    "Q863813": [PlaceCategory.MILITARY],                  # bunker
    "Q12518": [PlaceCategory.UNDERGROUND],                # tower
    "Q41176": [PlaceCategory.ARCHITECTURE],               # building
    "Q191992": [PlaceCategory.ABANDONED],                 # abandoned building
    "Q15893266": [PlaceCategory.LANDMARK],                # former entity
    "Q12280": [PlaceCategory.LANDMARK],                   # bridge
    "Q39614": [PlaceCategory.LANDMARK],                   # lighthouse
    "Q12323": [PlaceCategory.WATER, PlaceCategory.NATURE_HIDDEN],  # waterfall
    "Q124714": [PlaceCategory.WATER],                     # spring
}

_SPARQL_TEMPLATE = """
SELECT DISTINCT ?item ?itemLabel ?itemDescription ?coord ?sitelink ?type WHERE {{
  SERVICE wikibase:box {{
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerWest "Point({west} {south})"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerEast "Point({east} {north})"^^geo:wktLiteral .
  }}
  ?item wdt:P31 ?type .
  VALUES ?type {{ {type_values} }}
  OPTIONAL {{
    ?sitelink schema:about ?item ;
              schema:isPartOf <https://en.wikipedia.org/> .
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,ru,de,fr,es" . }}
}}
LIMIT 500
"""


class WikidataSource(BaseSource):
    """Discovers geo-located historical/hidden objects from Wikidata."""

    source_name = "wiki"

    async def search(self, lat: float, lng: float, radius_km: float) -> list[Place]:
        south, west, north, east = bounding_box(lat, lng, radius_km)
        cache_key = f"wiki_{south:.3f}_{west:.3f}_{north:.3f}_{east:.3f}"

        cached = await disk_cache.get(cache_key)
        if cached is not None:
            logger.debug("Wikidata cache hit")
            return [Place.model_validate(p) for p in cached]

        places = await self._query_sparql(south, west, north, east)
        await disk_cache.set(cache_key, [p.model_dump(mode="json") for p in places])
        logger.info("Wikidata returned %d places", len(places))
        return places

    async def _query_sparql(
        self, south: float, west: float, north: float, east: float
    ) -> list[Place]:
        type_values = " ".join(f"wd:{qid}" for qid in _TYPE_MAP)
        query = _SPARQL_TEMPLATE.format(
            south=south, west=west, north=north, east=east,
            type_values=type_values,
        )

        headers = {
            "User-Agent": "TerraIncognita/0.1 (personal research project)",
            "Accept": "application/sparql-results+json",
        }
        try:
            client = get_http_client()
            resp = await client.get(
                settings.wikidata_sparql_url,
                params={"query": query},
                headers=headers,
                timeout=settings.wikidata_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("Wikidata SPARQL failed: %s", exc)
            return []

        return self._parse_results(data)

    def _parse_results(self, data: dict) -> list[Place]:
        seen: set[str] = set()
        places: list[Place] = []

        for binding in data.get("results", {}).get("bindings", []):
            item_uri = binding.get("item", {}).get("value", "")
            qid = item_uri.rsplit("/", 1)[-1]
            if qid in seen:
                continue
            seen.add(qid)

            coord_str = binding.get("coord", {}).get("value", "")
            lat, lng = self._parse_point(coord_str)
            if lat is None:
                continue

            type_uri = binding.get("type", {}).get("value", "")
            type_qid = type_uri.rsplit("/", 1)[-1]
            categories = _TYPE_MAP.get(type_qid, [PlaceCategory.LANDMARK])

            name = binding.get("itemLabel", {}).get("value")
            description = binding.get("itemDescription", {}).get("value")
            wiki_url = binding.get("sitelink", {}).get("value")

            # Prefer less-popular items (those without Wikipedia articles score higher for unusualness)
            confidence = 0.6 if wiki_url else 0.7

            places.append(
                Place(
                    id=f"wiki_{qid}",
                    source=PlaceSource.WIKIDATA,
                    sources=[PlaceSource.WIKIDATA],
                    name=name if name != qid else None,
                    description=description,
                    categories=categories,
                    coordinates=Coordinates(lat=lat, lng=lng),
                    confidence=confidence,
                    metadata={
                        "wikidata_id": qid,
                        "wikipedia_url": wiki_url,
                        "type_qid": type_qid,
                    },
                )
            )
        return places

    @staticmethod
    def _parse_point(wkt: str) -> tuple[float | None, float | None]:
        """Parse 'Point(lng lat)' WKT string."""
        if not wkt.startswith("Point("):
            return None, None
        try:
            inner = wkt[6:-1]  # strip "Point(" and ")"
            lng_s, lat_s = inner.split()
            return float(lat_s), float(lng_s)
        except (ValueError, IndexError):
            return None, None
