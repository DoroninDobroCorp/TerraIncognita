"""Story 1.1 — OSM/Overpass Integration.

Queries the Overpass API for unusual and interesting places (abandoned / disused / ruined
objects, bunkers, mines, tunnels, caves, industrial heritage ... plus a layer of classic
landmarks) and normalises them into the unified Place model.

Tag interpretation (categories, lifecycle, generated names, photo links) lives in
:mod:`app.sources.osm_tags` so scoring and tests share it.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import settings
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.cache import disk_cache
from app.sources.base import BaseSource, PartialSourceResult, SourceError
from app.sources.osm_tags import (
    commons_category,
    extract_photos,
    interpret_tags,
    is_street_not_place,
    meaningful_tag_list,
    parse_wikidata_id,
    parse_wikipedia_tag,
    pick_description,
    pick_name,
    wikipedia_url,
)
from app.utils.geo import bounding_box
from app.utils.http_client import get_http_client
from app.utils.rate_limiter import RateLimiter
from app.utils.single_flight import SingleFlight

logger = logging.getLogger(__name__)

# ── Overpass query ─────────────────────────────────────────────────────────
#
# Two tiers, queried concurrently so a slow/failed tier never loses the other:
#   * gems       — the "hidden places" classes (always queried)
#   * landmarks  — mainstream context (museums, memorials, viewpoints ...), local searches only
#
# Statements only use plain key / key=value / value-regex filters (index lookups). A regex on the
# *key* (``[~"^abandoned:"~"."]``) forces a scan of every object in the bbox and measured >20 s
# for a 4 km box, so lifecycle prefixes are enumerated explicitly instead. Mundane sub-classes
# (a closed kiosk, a culvert, a radio mast, a street that used to be a railway) are excluded
# up-front so they do not eat the element cap.

# Objects that are merely "closed for business" or roads, not places to explore.
_NOT_A_PLACE = (
    '["shop"!~"."]["highway"!~"."]'
    '["amenity"!~"^(cafe|restaurant|bar|pub|fast_food|bank|pharmacy|atm|parking|parking_space|'
    'bench|waste_basket|bicycle_parking|vending_machine|toilets|post_box|recycling|kiosk)$"]'
)
# A former railway bed that is now an ordinary street is a street, not a place.
_NOT_A_STREET = (
    '["highway"!~"^(residential|tertiary|secondary|primary|trunk|motorway|service|unclassified|'
    'living_street)$"]'
)
_REAL_TUNNEL = '["tunnel"]["tunnel"!~"^(no|culvert|building_passage|flooded|passage)$"]'
_DEAD_RAILWAY = '"railway"~"^(abandoned|disused|disused_station)$"'

# What an ``abandoned:*`` / ``disused:*`` prefix may have been (any value).
_FORMER_KEYS = (
    "building", "man_made", "military", "aeroway", "industrial", "power", "historic",
    "tourism", "leisure", "place",
)
_FORMER_AMENITIES = (
    "school|hospital|clinic|prison|cinema|theatre|college|university|kindergarten|"
    "place_of_worship|monastery|courthouse|townhall|ferry_terminal|bus_station|fuel|"
    "swimming_pool|arts_centre|community_centre|fire_station|police|public_bath|baths|sanatorium"
)
_FORMER_STATIONS = "station|halt|tram_stop|depot|yard|roundhouse|turntable|engine_shed"
_FORMER_LANDUSE = "quarry|military|industrial|railway|brownfield|landfill"


def _lifecycle_clauses() -> tuple[str, ...]:
    clauses = [
        # abandoned / disused / ruined, on the object itself ...
        f'nwr["abandoned"]["abandoned"!~"^(no|false|0)$"]{_NOT_A_PLACE}',
        f'nwr["disused"]["disused"!~"^(no|false|0)$"]{_NOT_A_PLACE}',
        f'nwr["ruins"]["ruins"!~"^(no|false|0)$"]{_NOT_A_PLACE}',
        'nwr["building"~"^(ruins|collapsed)$"]',
        'nwr["ruined"="yes"]',
        'nwr["ruins:building"]',
    ]
    # ... or as a prefix on what it used to be (abandoned:building=*, disused:railway=station ...)
    for prefix in ("abandoned", "disused"):
        clauses += [f'nwr["{prefix}:{key}"]' for key in _FORMER_KEYS]
        clauses += [
            f'nwr["{prefix}:railway"~"^({_FORMER_STATIONS})$"]',
            f'nwr["{prefix}:amenity"~"^({_FORMER_AMENITIES})$"]',
            f'nwr["{prefix}:landuse"~"^({_FORMER_LANDUSE})$"]',
        ]
    return tuple(clauses)


# Always queried: the "hidden places" classes.
_GEM_CLAUSES: tuple[str, ...] = _lifecycle_clauses() + (
    # historic objects
    'nwr["historic"~"^(ruins|archaeological_site|fort|fortification|castle|bunker|mine|mine_shaft|'
    "charcoal_pile|wreck|battlefield|manor|city_gate|citywalls|tomb|tumulus|barrow|megalith|menhir|"
    'ship|aircraft|tank|locomotive|railway_car|cannon|monastery|lime_kiln|kiln|gallows|pillory|'
    'rune_stone|stone|pyramid)$"]',
    # military
    'nwr["military"~"^(bunker|barracks|airfield|trench|naval_base|checkpoint|'
    'nuclear_explosion_site|ammunition|launchpad|base)$"]',
    'nwr["building"="bunker"]',
    # man-made
    'nwr["man_made"~"^(adit|mineshaft|tunnel|water_tower|lighthouse|silo|windmill|watermill|kiln|'
    'obelisk|observatory|reservoir_covered)$"]',
    'nwr["man_made"="chimney"]["name"]',
    'nwr["man_made"="tower"]["tower:type"~"^(observation|watchtower|lookout|defensive|keep|'
    'cooling|water|bell_tower|round|square|fortified)$"]',
    'nwr["man_made"="tower"]["name"]["tower:type"!~"^(communication|radio|tv)$"]',
    # tunnels (but not culverts, underpasses or ordinary road tunnels)
    f'nwr{_REAL_TUNNEL}["tunnel:name"]',
    f'nwr{_REAL_TUNNEL}["highway"~"^(footway|path|pedestrian|cycleway|track|steps|bridleway)$"]["name"]',
    f'nwr[{_DEAD_RAILWAY}]["tunnel"]',
    # dead railways: stations (nodes), named lines (not streets), lines carrying a bridge
    f"node[{_DEAD_RAILWAY}]",
    f'nwr[{_DEAD_RAILWAY}]["name"]{_NOT_A_STREET}',
    f'way[{_DEAD_RAILWAY}]["bridge"]',
    # caves & other natural curiosities
    'nwr["natural"~"^(cave_entrance|sinkhole|hot_spring|geyser|arch|volcano)$"]',
    'nwr["natural"="tree"]["denotation"="landmark"]',
    'nwr["waterway"~"^(dam|waterfall)$"]',
    # industrial heritage
    'nwr["industrial"~"^(mine|shipyard|factory|steel_mill|brickyard|cement|refinery|oil|'
    'distillery|brewery|sawmill|glass|chemical|ironworks|slaughterhouse|salt)$"]',
)

# Mainstream landmarks: useful context, but heavy and not "hidden", so only for local searches.
_LANDMARK_CLAUSES: tuple[str, ...] = (
    'nwr["natural"~"^(spring|waterfall|peak|cliff|rock|stone|gorge)$"]["name"]',
    'nwr["leisure"="nature_reserve"]["name"]',
    'nwr["historic"~"^(monument|memorial)$"]["name"]',
    'nwr["tourism"~"^(museum|viewpoint|attraction)$"]',
    'nwr["tourism"="artwork"]["artwork_type"~"^(mural|graffiti|street_art|installation|mosaic)$"]',
    'nwr["tourism"="artwork"]["wikidata"]',
    'nwr["building"="cathedral"]',
    # Notable restaurants & hotels (only with wikipedia/heritage/historic — truly outstanding)
    'nwr["amenity"="restaurant"]["wikipedia"]',
    'nwr["amenity"="restaurant"]["heritage"]',
    'nwr["amenity"="restaurant"]["historic"]',
    'nwr["tourism"="hotel"]["wikipedia"]',
    'nwr["tourism"="hotel"]["heritage"]',
    'nwr["tourism"="hotel"]["historic"]',
    'nwr["tourism"="hotel"]["stars"~"^[4-5]$"]',
)


def build_overpass_query(
    bbox: str,
    timeout: int,
    clauses: tuple[str, ...] | None = None,
    *,
    max_elements: int | None = None,
) -> str:
    """Overpass QL for ``clauses`` (default: every tier) inside ``bbox`` ("south,west,north,east")."""
    chosen = _GEM_CLAUSES + _LANDMARK_CLAUSES if clauses is None else clauses
    body = "\n".join(f"  {c}({bbox});" for c in chosen)
    limit = f" {max_elements}" if max_elements else ""
    return f"[out:json][timeout:{timeout}];\n(\n{body}\n);\nout center tags{limit};\n"


class OverpassError(SourceError):
    """Overpass answered, but not with a usable result (timeout remark, overload, bad status)."""


class OSMSource(BaseSource):
    """Fetches unusual and landmark places from OpenStreetMap via Overpass API."""

    source_name = "osm"

    def __init__(self) -> None:
        self._rate_limiter = RateLimiter(
            max_requests=settings.overpass_rate_limit_requests,
            window_seconds=settings.overpass_rate_limit_window_seconds,
        )
        self._in_flight = SingleFlight()

    async def search(self, lat: float, lng: float, radius_km: float) -> list[Place]:
        south, west, north, east = bounding_box(lat, lng, radius_km)
        # ~100 m grid: a reload with a slightly different GPS fix must hit the entry that an
        # earlier (possibly still running) request fills, and the cache stays reusable.
        bbox = f"{south:.3f},{west:.3f},{north:.3f},{east:.3f}"

        tiers: list[tuple[str, tuple[str, ...]]] = [("gems", _GEM_CLAUSES)]
        if radius_km <= settings.overpass_landmark_max_radius_km:
            tiers.append(("landmarks", _LANDMARK_CLAUSES))

        # Tiers run concurrently; one failing tier must not lose the other's places.
        results = await asyncio.gather(
            *(self._search_tier(name, clauses, bbox) for name, clauses in tiers),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        if len(errors) == len(results):
            raise errors[0]
        for err in errors:
            logger.warning("OSM tier failed (partial result returned): %s", err)

        places: dict[str, Place] = {}
        incomplete: set[str] = set()  # tiers that failed or came back with a server "remark"
        for (name, _), result in zip(tiers, results, strict=True):
            if isinstance(result, BaseException):
                incomplete.add(name)
                continue
            batch, complete = result
            if not complete:
                incomplete.add(name)
            for place in batch:
                places.setdefault(place.id, place)
        logger.info("OSM returned %d places for bbox=%s", len(places), bbox)
        # Losing the landmark tier is cosmetic; an incomplete gems tier means hidden places the
        # app exists for may be missing, and the caller should say so rather than report "ok".
        if "gems" in incomplete:
            raise PartialSourceResult(
                list(places.values()), "the query for hidden places failed or was cut short"
            )
        return list(places.values())

    async def _search_tier(
        self, tier: str, clauses: tuple[str, ...], bbox: str
    ) -> tuple[list[Place], bool]:
        """(places, complete). ``complete`` is False when Overpass flagged the answer as partial."""
        # "osm2": entries written by the pre-tier query (different shape) are not reused.
        cache_key = f"osm2_{tier}_{bbox}"
        cached = await disk_cache.get(cache_key)
        if cached is not None:
            logger.debug("OSM cache hit for %s", cache_key)
            return [Place.model_validate(p) for p in cached], True

        # A retry while an earlier request is still waiting on Overpass shares that request
        # instead of sending the same heavy query again.
        return await self._in_flight.run(
            cache_key, lambda: self._fetch_tier(tier, clauses, bbox, cache_key)
        )

    async def _fetch_tier(
        self, tier: str, clauses: tuple[str, ...], bbox: str, cache_key: str
    ) -> tuple[list[Place], bool]:
        query = build_overpass_query(
            bbox, settings.overpass_timeout, clauses,
            max_elements=settings.overpass_max_elements,
        )
        data = await self._run_query(query)
        elements = data.get("elements", [])
        if len(elements) >= settings.overpass_max_elements:
            logger.warning(
                "OSM %s result hit the %d element cap for bbox=%s; consider a smaller radius",
                tier, settings.overpass_max_elements, bbox,
            )
        places = self._parse_elements(elements)
        complete = not data.get("remark")  # a remark means a partial result: do not cache it
        if complete:
            await disk_cache.set(cache_key, [p.model_dump(mode="json") for p in places])
        return places, complete

    # ------------------------------------------------------------------

    async def _run_query(self, query: str) -> dict:
        """POST the query to the primary Overpass endpoint, falling back to mirrors."""
        urls = [settings.overpass_url] + [
            u.strip() for u in settings.overpass_fallback_urls.split(",") if u.strip()
        ]
        client = get_http_client()
        last_error: Exception | None = None
        for url in urls:
            await self._rate_limiter.acquire()
            try:
                resp = await client.post(
                    url,
                    data={"data": query},
                    timeout=settings.overpass_timeout + 10,  # server-side [timeout] fires first
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("Overpass %s failed: %s", url, exc)
                last_error = exc
                continue
            remark = str(data.get("remark", ""))
            if "runtime error" in remark.lower() and not data.get("elements"):
                # HTTP 200 with an error remark: the query timed out / ran out of memory.
                logger.warning("Overpass %s: %s", url, remark)
                last_error = OverpassError(remark)
                continue
            return data
        raise OverpassError(f"all Overpass endpoints failed: {last_error}") from last_error

    # ------------------------------------------------------------------

    def _parse_elements(self, elements: list[dict]) -> list[Place]:
        places: list[Place] = []
        for el in elements:
            lat, lng = self._extract_coords(el)
            if lat is None:
                continue
            tags = el.get("tags") or {}
            if not tags or is_street_not_place(tags):
                continue  # untagged helper element / a street that used to be a railway
            places.append(self._to_place(el, tags, lat, lng))
        return places

    def _to_place(self, el: dict, tags: dict, lat: float, lng: float) -> Place:
        info = interpret_tags(tags)
        categories = list(info.categories)
        osm_type = el.get("type", "node")
        osm_id = f"osm_{osm_type}_{el['id']}"

        name = pick_name(tags)
        name_generated = False
        if name is None and info.generated_name:
            name, name_generated = info.generated_name, True

        metadata: dict = {
            "osm_type": el.get("type"),
            "osm_id": el.get("id"),
            "osm_url": f"https://www.openstreetmap.org/{osm_type}/{el['id']}",
            "osm_tags": tags,
        }
        if name_generated:
            metadata["name_generated"] = True
        if info.lifecycle:
            metadata["lifecycle"] = info.lifecycle
        if info.feature:
            metadata["osm_feature"] = info.feature

        for meta_key, value in (
            ("name_en", tags.get("name:en")),
            ("name_ru", tags.get("name:ru")),
            ("access", tags.get("access")),
            ("start_date", tags.get("start_date")),
            ("historic_period", tags.get("historic:period") or tags.get("historic:civilization")),
            ("heritage", tags.get("heritage")),
            ("operator", tags.get("operator")),
            ("opening_hours", tags.get("opening_hours")),
            ("website", tags.get("website") or tags.get("contact:website") or tags.get("url")),
            ("wikimedia_commons", tags.get("wikimedia_commons")),
            ("image", tags.get("image")),
        ):
            if value:
                metadata[meta_key] = value
        if name and tags.get("name:en") == name:
            metadata.pop("name_en", None)
        alt_names = [
            v.strip()
            for k in ("alt_name", "old_name", "official_name", "int_name")
            for v in (tags.get(k) or "").split(";")
            if v.strip() and v.strip() != name
        ]
        if alt_names:
            metadata["alt_names"] = list(dict.fromkeys(alt_names))
        if (qid := parse_wikidata_id(tags.get("wikidata"))) is not None:
            metadata["wikidata_id"] = qid
        if (wiki := parse_wikipedia_tag(tags.get("wikipedia"))) is not None:
            metadata["wikipedia_lang"], metadata["wikipedia_title"] = wiki
            metadata["wikipedia_url"] = wikipedia_url(*wiki)
        if (category := commons_category(tags)) is not None:
            metadata["commons_category"] = category

        confidence = 0.7 if categories else 0.4
        if name_generated or name is None:
            confidence -= 0.1  # unnamed: still returned, but ranked below documented places

        return Place(
            id=osm_id,
            source=PlaceSource.OSM,
            sources=[PlaceSource.OSM],
            name=name,
            description=pick_description(tags),
            categories=categories,
            coordinates=Coordinates(lat=lat, lng=lng),
            confidence=round(confidence, 2),
            # Categories read straight from tags are explicit: let the keyword classifier
            # leave them alone (it only fills in places that have none).
            category_confidence={c.value: 0.85 for c in categories},
            tags=self._extract_tag_list(tags),
            photos=extract_photos(tags),
            metadata=metadata,
        )

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
        return list(interpret_tags(tags).categories)

    @staticmethod
    def _extract_tag_list(tags: dict) -> list[str]:
        """Extract meaningful OSM tags as a flat list of strings."""
        return meaningful_tag_list(tags)
