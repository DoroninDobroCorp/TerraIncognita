"""Story 1.3 — Wikidata/Wikipedia Geo-entities.

SPARQL queries to Wikidata for historical and hidden objects within a bounding box,
enriched with Wikipedia article summaries.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.cache import disk_cache
from app.sources.base import BaseSource, PartialSourceResult
from app.utils.geo import bounding_box
from app.utils.http_client import get_http_client
from app.utils.single_flight import SingleFlight

logger = logging.getLogger(__name__)

# Wikidata type (P31 value) → PlaceCategory. Every Q-id below was checked against wbgetentities
# (the previous table had several wrong ids: "ruin" was the id of city gates, "bunker" of a
# university, "abandoned building" of headlands, "lighthouse" of cemeteries).
_TYPE_MAP: dict[str, list[PlaceCategory]] = {
    # hidden / forgotten
    "Q109607": [PlaceCategory.RUINS],                                  # ruins
    "Q839954": [PlaceCategory.RUINS],                                  # archaeological site
    "Q106765618": [PlaceCategory.ABANDONED],                           # abandoned building or structure
    "Q4663385": [PlaceCategory.ABANDONED, PlaceCategory.TRANSPORT],    # former railway station
    "Q74047": [PlaceCategory.ABANDONED],                               # ghost town
    "Q91122": [PlaceCategory.MILITARY, PlaceCategory.UNDERGROUND],     # bunker
    "Q389959": [PlaceCategory.MILITARY, PlaceCategory.UNDERGROUND],    # air-raid shelter
    "Q20788294": [PlaceCategory.MILITARY, PlaceCategory.UNDERGROUND],  # air-raid shelter adit
    "Q245016": [PlaceCategory.MILITARY],                               # military base
    "Q820477": [PlaceCategory.INDUSTRIAL, PlaceCategory.UNDERGROUND],  # mine
    "Q44377": [PlaceCategory.UNDERGROUND],                             # tunnel
    "Q35509": [PlaceCategory.CAVE, PlaceCategory.UNDERGROUND],         # cave
    "Q852190": [PlaceCategory.UNDERWATER],                             # shipwreck
    "Q274153": [PlaceCategory.INDUSTRIAL, PlaceCategory.ARCHITECTURE],  # water tower
    "Q38720": [PlaceCategory.INDUSTRIAL, PlaceCategory.ARCHITECTURE],  # windmill
    "Q12323": [PlaceCategory.WATER],                                   # dam
    "Q34038": [PlaceCategory.WATER, PlaceCategory.NATURE_HIDDEN],      # waterfall
    "Q124714": [PlaceCategory.WATER],                                  # spring
    # fortifications & classic landmarks
    "Q57821": [PlaceCategory.MILITARY, PlaceCategory.LANDMARK],        # fortification
    "Q1785071": [PlaceCategory.MILITARY, PlaceCategory.LANDMARK],      # fort
    "Q23413": [PlaceCategory.LANDMARK, PlaceCategory.ARCHITECTURE],    # castle
    "Q82117": [PlaceCategory.LANDMARK, PlaceCategory.ARCHITECTURE],    # city gate
    "Q16970": [PlaceCategory.RELIGIOUS],                               # church building
    "Q32815": [PlaceCategory.RELIGIOUS],                               # mosque
    "Q34627": [PlaceCategory.RELIGIOUS],                               # synagogue
    "Q33506": [PlaceCategory.MUSEUM],                                  # museum
    "Q4989906": [PlaceCategory.MONUMENT],                              # monument
    "Q12518": [PlaceCategory.ARCHITECTURE],                            # tower
    "Q41176": [PlaceCategory.ARCHITECTURE],                            # building
    "Q15893266": [PlaceCategory.LANDMARK],                             # former entity
    "Q12280": [PlaceCategory.LANDMARK],                                # bridge
    "Q39715": [PlaceCategory.LANDMARK, PlaceCategory.ARCHITECTURE],    # lighthouse
}

_SPARQL_TEMPLATE = """
SELECT DISTINCT ?item ?itemLabel ?itemDescription ?coord ?sitelink ?sitelinks ?type WHERE {{
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
  OPTIONAL {{ ?item wikibase:sitelinks ?sitelinks . }}
  BIND(IF(?type IN ({common_values}), 1, 0) AS ?common)
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,ru,de,fr,es" . }}
}}
ORDER BY ?common
LIMIT {limit}
"""

_QUERY_LIMIT = 1500
# Everyday buildings and landmarks: in a city centre they number in the thousands (Vienna, 10 km:
# 1,084 plain "building" items against one ruin). They sort last, so when the row limit cuts the
# answer off it is these that go, not the rare types.
_COMMON_TYPES = ("Q41176", "Q12518", "Q15893266", "Q12280", "Q16970", "Q32815", "Q34627", "Q33506", "Q4989906")


def _generated_name(description: str | None, categories: list[PlaceCategory]) -> str:
    """Honest display name for an unlabelled item: its own description ("cave in Herceg Novi")
    or, failing that, its category ("Cave"). Flagged ``name_generated`` so ranking and
    fusion treat the place as unnamed."""
    if description and description.strip():
        text = description.strip()
        return text[0].upper() + text[1:]
    return categories[0].value.replace("_", " ").capitalize() if categories else "Unnamed place"


class WikidataSource(BaseSource):
    """Discovers geo-located historical/hidden objects from Wikidata."""

    source_name = "wiki"

    def __init__(self) -> None:
        self._in_flight = SingleFlight()

    async def search(self, lat: float, lng: float, radius_km: float) -> list[Place]:
        south, west, north, east = bounding_box(lat, lng, radius_km)
        # "wiki2": entries written before completeness was tracked (plain lists) are not reused.
        cache_key = f"wiki2_{south:.3f}_{west:.3f}_{north:.3f}_{east:.3f}"

        cached = await disk_cache.get(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("places"), list):
            logger.debug("Wikidata cache hit")
            places = [Place.model_validate(p) for p in cached["places"]]
            complete = bool(cached.get("complete", True))
        else:
            async def fetch() -> tuple[list[Place], bool]:
                found, complete = await self._query_sparql(south, west, north, east)
                await disk_cache.set(
                    cache_key,
                    {"places": [p.model_dump(mode="json") for p in found], "complete": complete},
                )
                logger.info("Wikidata returned %d places%s", len(found), "" if complete else " (truncated)")
                return found, complete

            places, complete = await self._in_flight.run(cache_key, fetch)
        if not complete:
            raise PartialSourceResult(places, f"the {_QUERY_LIMIT}-row limit cut the answer off")
        return places

    async def _query_sparql(
        self, south: float, west: float, north: float, east: float
    ) -> tuple[list[Place], bool]:
        """(places, complete). ``complete`` is False when the row limit cut the answer off."""
        type_values = " ".join(f"wd:{qid}" for qid in _TYPE_MAP)
        query = _SPARQL_TEMPLATE.format(
            south=south, west=west, north=north, east=east,
            type_values=type_values, limit=_QUERY_LIMIT,
            common_values=", ".join(f"wd:{qid}" for qid in _COMMON_TYPES),
        )

        headers = {
            "User-Agent": settings.wikimedia_user_agent,
            "Accept": "application/sparql-results+json",
        }
        # Failures propagate (discovery reports the source as "error"); returning [] here would
        # get an outage cached for a day as "no places nearby".
        client = get_http_client()
        resp = await client.get(
            settings.wikidata_sparql_url,
            params={"query": query},
            headers=headers,
            timeout=settings.wikidata_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = len(data.get("results", {}).get("bindings", []))
        return self._parse_results(data), rows < _QUERY_LIMIT

    def _parse_results(self, data: dict) -> list[Place]:
        by_qid: dict[str, Place] = {}
        places: list[Place] = []

        for binding in data.get("results", {}).get("bindings", []):
            item_uri = binding.get("item", {}).get("value", "")
            qid = item_uri.rsplit("/", 1)[-1]
            if qid in by_qid:
                # another type row of an item we already have: its categories join the first row's
                # (SPARQL row order is not stable, so "first row wins" would be arbitrary)
                known = by_qid[qid]
                type_qid = binding.get("type", {}).get("value", "").rsplit("/", 1)[-1]
                for cat in _TYPE_MAP.get(type_qid, ()):
                    if cat not in known.categories:
                        known.categories.append(cat)
                continue

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
            sitelinks = self._parse_count(binding.get("sitelinks", {}).get("value"))

            # Prefer less-popular items (those without Wikipedia articles score higher for unusualness)
            confidence = 0.6 if wiki_url else 0.7

            # Wikidata answers with the bare Q-id when an item has no label in our languages.
            name = name if name != qid else None
            name_generated = name is None
            if name_generated:
                name = _generated_name(description, categories)

            place = (
                Place(
                    id=f"wiki_{qid}",
                    source=PlaceSource.WIKIDATA,
                    sources=[PlaceSource.WIKIDATA],
                    name=name,
                    description=description,
                    categories=categories,
                    coordinates=Coordinates(lat=lat, lng=lng),
                    confidence=confidence,
                    metadata={
                        "wikidata_id": qid,
                        "wikipedia_url": wiki_url,
                        "type_qid": type_qid,
                        **({"name_generated": True} if name_generated else {}),
                        # How many Wikipedia language editions cover it: a mainstream-ness signal.
                        **({"sitelinks": sitelinks} if sitelinks is not None else {}),
                    },
                )
            )
            by_qid[qid] = place
            places.append(place)
        return places

    @staticmethod
    def _parse_count(value: str | None) -> int | None:
        try:
            return max(int(value), 0) if value is not None else None
        except ValueError:
            return None

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
