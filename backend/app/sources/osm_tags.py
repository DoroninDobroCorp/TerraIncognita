"""Interpretation of OpenStreetMap tags: what a feature *is*, whether it is abandoned/ruined,
which :class:`PlaceCategory` values it maps to and how to name it when it has no ``name``.

Pure functions only (no I/O) so the OSM source, the scoring service and the tests share one
definition of "what does this tag set mean".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

from app.models.place import PlaceCategory

C = PlaceCategory


@dataclass(frozen=True)
class Feature:
    """What a ``key=value`` OSM tag says an object is."""

    categories: tuple[PlaceCategory, ...]
    noun: str  # lower-case English noun used for generated names ("bunker", "water tower")


# key=value → Feature. A feature with no categories only contributes a noun for naming.
FEATURES: dict[str, Feature] = {
    # ── military ────────────────────────────────────────────────
    "military=bunker": Feature((C.MILITARY, C.UNDERGROUND), "bunker"),
    "military=barracks": Feature((C.MILITARY,), "barracks"),
    "military=airfield": Feature((C.MILITARY, C.TRANSPORT), "military airfield"),
    "military=trench": Feature((C.MILITARY,), "trench"),
    "military=naval_base": Feature((C.MILITARY,), "naval base"),
    "military=checkpoint": Feature((C.MILITARY,), "checkpoint"),
    "military=nuclear_explosion_site": Feature((C.MILITARY,), "nuclear test site"),
    "military=ammunition": Feature((C.MILITARY,), "ammunition depot"),
    "military=launchpad": Feature((C.MILITARY,), "launch site"),
    "military=base": Feature((C.MILITARY,), "military base"),
    "military=training_area": Feature((C.MILITARY,), "training area"),
    "building=bunker": Feature((C.MILITARY,), "bunker"),
    "historic=bunker": Feature((C.MILITARY,), "bunker"),
    "landuse=military": Feature((C.MILITARY,), "military area"),
    # ── historic ────────────────────────────────────────────────
    "historic=ruins": Feature((C.RUINS,), "ruins"),
    "building=ruins": Feature((C.RUINS,), "ruins"),
    "building=collapsed": Feature((C.RUINS,), "ruins"),
    "historic=archaeological_site": Feature((C.RUINS,), "archaeological site"),
    "historic=fort": Feature((C.LANDMARK, C.MILITARY), "fort"),
    "historic=fortification": Feature((C.LANDMARK, C.MILITARY), "fortification"),
    "historic=citywalls": Feature((C.LANDMARK, C.MILITARY), "city walls"),
    "historic=city_gate": Feature((C.LANDMARK, C.ARCHITECTURE), "city gate"),
    "historic=castle": Feature((C.LANDMARK, C.ARCHITECTURE), "castle"),
    "historic=manor": Feature((C.ARCHITECTURE, C.LANDMARK), "manor"),
    "historic=battlefield": Feature((C.MILITARY, C.MONUMENT), "battlefield"),
    "historic=mine": Feature((C.INDUSTRIAL, C.UNDERGROUND), "old mine"),
    "historic=mine_shaft": Feature((C.INDUSTRIAL, C.UNDERGROUND), "old mine shaft"),
    "historic=charcoal_pile": Feature((C.INDUSTRIAL, C.RUINS), "charcoal-burning site"),
    "historic=lime_kiln": Feature((C.INDUSTRIAL, C.RUINS), "lime kiln"),
    "historic=kiln": Feature((C.INDUSTRIAL, C.RUINS), "kiln"),
    "historic=wreck": Feature((C.UNDERWATER, C.RUINS), "shipwreck"),
    "historic=ship": Feature((C.TRANSPORT, C.MONUMENT), "historic ship"),
    "historic=aircraft": Feature((C.TRANSPORT, C.MONUMENT), "historic aircraft"),
    "historic=tank": Feature((C.MILITARY, C.MONUMENT), "historic tank"),
    "historic=cannon": Feature((C.MILITARY, C.MONUMENT), "historic cannon"),
    "historic=locomotive": Feature((C.TRANSPORT, C.MONUMENT), "historic locomotive"),
    "historic=railway_car": Feature((C.TRANSPORT, C.MONUMENT), "historic railway car"),
    "historic=tomb": Feature((C.MONUMENT, C.RUINS), "tomb"),
    "historic=tumulus": Feature((C.RUINS,), "burial mound"),
    "historic=barrow": Feature((C.RUINS,), "burial mound"),
    "historic=megalith": Feature((C.RUINS, C.NATURE_HIDDEN), "megalith"),
    "historic=menhir": Feature((C.RUINS, C.NATURE_HIDDEN), "menhir"),
    "historic=rune_stone": Feature((C.RUINS, C.MONUMENT), "rune stone"),
    "historic=stone": Feature((C.MONUMENT,), "historic stone"),
    "historic=pyramid": Feature((C.MONUMENT,), "pyramid"),
    "historic=gallows": Feature((C.MONUMENT,), "gallows"),
    "historic=pillory": Feature((C.MONUMENT,), "pillory"),
    "historic=monastery": Feature((C.RELIGIOUS,), "monastery"),
    "historic=monument": Feature((C.MONUMENT,), "monument"),
    "historic=memorial": Feature((C.MONUMENT,), "memorial"),
    "historic=building": Feature((), "historic building"),
    # ── man_made ────────────────────────────────────────────────
    "man_made=adit": Feature((C.UNDERGROUND, C.INDUSTRIAL), "mine adit"),
    "man_made=mineshaft": Feature((C.UNDERGROUND, C.INDUSTRIAL), "mine shaft"),
    "man_made=tunnel": Feature((C.UNDERGROUND,), "tunnel"),
    "man_made=water_tower": Feature((C.INDUSTRIAL, C.ARCHITECTURE), "water tower"),
    "man_made=lighthouse": Feature((C.LANDMARK, C.ARCHITECTURE), "lighthouse"),
    "man_made=silo": Feature((C.INDUSTRIAL,), "silo"),
    "man_made=storage_tank": Feature((C.INDUSTRIAL,), "storage tank"),
    "man_made=chimney": Feature((C.INDUSTRIAL,), "chimney"),
    "man_made=windmill": Feature((C.INDUSTRIAL, C.ARCHITECTURE), "windmill"),
    "man_made=watermill": Feature((C.INDUSTRIAL, C.ARCHITECTURE), "watermill"),
    "man_made=kiln": Feature((C.INDUSTRIAL,), "kiln"),
    "man_made=works": Feature((C.INDUSTRIAL,), "factory"),
    "man_made=obelisk": Feature((C.MONUMENT,), "obelisk"),
    "man_made=observatory": Feature((C.LANDMARK,), "observatory"),
    "man_made=reservoir_covered": Feature((C.UNDERGROUND, C.WATER), "covered reservoir"),
    "man_made=tower": Feature((C.VIEWPOINT,), "tower"),
    "man_made=pier": Feature((), "pier"),
    "man_made=bridge": Feature((), "bridge"),
    "man_made=pumping_station": Feature((C.INDUSTRIAL,), "pumping station"),
    # ── natural / waterway ──────────────────────────────────────
    "natural=cave_entrance": Feature((C.CAVE, C.UNDERGROUND), "cave entrance"),
    "natural=sinkhole": Feature((C.NATURE_HIDDEN, C.CAVE), "sinkhole"),
    "natural=hot_spring": Feature((C.WATER, C.NATURE_HIDDEN), "hot spring"),
    "natural=geyser": Feature((C.WATER, C.NATURE_HIDDEN), "geyser"),
    "natural=arch": Feature((C.NATURE_HIDDEN,), "natural arch"),
    "natural=volcano": Feature((C.NATURE_HIDDEN, C.VIEWPOINT), "volcano"),
    "natural=spring": Feature((C.WATER, C.NATURE_HIDDEN), "spring"),
    "natural=waterfall": Feature((C.WATER, C.NATURE_HIDDEN), "waterfall"),
    "natural=peak": Feature((C.VIEWPOINT, C.NATURE_HIDDEN), "peak"),
    "natural=cliff": Feature((C.NATURE_HIDDEN, C.VIEWPOINT), "cliff"),
    "natural=rock": Feature((C.NATURE_HIDDEN,), "rock"),
    "natural=stone": Feature((C.NATURE_HIDDEN,), "boulder"),
    "natural=gorge": Feature((C.NATURE_HIDDEN,), "gorge"),
    "natural=tree": Feature((C.NATURE_HIDDEN,), "tree"),
    "waterway=dam": Feature((C.WATER,), "dam"),
    "waterway=waterfall": Feature((C.WATER, C.NATURE_HIDDEN), "waterfall"),
    "leisure=nature_reserve": Feature((C.PARK, C.NATURE_HIDDEN), "nature reserve"),
    # ── railway / transport ─────────────────────────────────────
    "railway=abandoned": Feature((C.TRANSPORT, C.ABANDONED), "railway line"),
    "railway=disused": Feature((C.TRANSPORT, C.ABANDONED), "railway line"),
    "railway=disused_station": Feature((C.TRANSPORT, C.ABANDONED), "railway station"),
    "railway=station": Feature((C.TRANSPORT,), "railway station"),
    "railway=halt": Feature((C.TRANSPORT,), "railway halt"),
    "railway=depot": Feature((C.TRANSPORT, C.INDUSTRIAL), "railway depot"),
    "railway=roundhouse": Feature((C.TRANSPORT, C.INDUSTRIAL), "railway roundhouse"),
    "railway=turntable": Feature((C.TRANSPORT, C.INDUSTRIAL), "railway turntable"),
    "railway=engine_shed": Feature((C.TRANSPORT, C.INDUSTRIAL), "engine shed"),
    "aeroway=aerodrome": Feature((C.TRANSPORT,), "airfield"),
    "aeroway=helipad": Feature((C.TRANSPORT,), "helipad"),
    "aeroway=hangar": Feature((C.TRANSPORT,), "hangar"),
    "amenity=bus_station": Feature((C.TRANSPORT,), "bus station"),
    "amenity=ferry_terminal": Feature((C.TRANSPORT,), "ferry terminal"),
    # ── industry ────────────────────────────────────────────────
    "building=industrial": Feature((C.INDUSTRIAL,), "industrial building"),
    "building=factory": Feature((C.INDUSTRIAL,), "factory"),
    "building=manufacture": Feature((C.INDUSTRIAL,), "factory"),
    "building=warehouse": Feature((C.INDUSTRIAL,), "warehouse"),
    "landuse=quarry": Feature((C.INDUSTRIAL,), "quarry"),
    "landuse=industrial": Feature((C.INDUSTRIAL,), "industrial area"),
    "landuse=railway": Feature((C.TRANSPORT,), "railway land"),
    "power=plant": Feature((C.INDUSTRIAL,), "power plant"),
    "power=substation": Feature((C.INDUSTRIAL,), "substation"),
    "power=generator": Feature((C.INDUSTRIAL,), "power generator"),
    # ── classic landmarks / culture ─────────────────────────────
    "amenity=place_of_worship": Feature((C.RELIGIOUS,), "place of worship"),
    "amenity=monastery": Feature((C.RELIGIOUS,), "monastery"),
    "building=cathedral": Feature((C.RELIGIOUS, C.ARCHITECTURE), "cathedral"),
    "building=church": Feature((C.RELIGIOUS,), "church"),
    "building=chapel": Feature((C.RELIGIOUS,), "chapel"),
    "building=mosque": Feature((C.RELIGIOUS,), "mosque"),
    "building=synagogue": Feature((C.RELIGIOUS,), "synagogue"),
    "building=temple": Feature((C.RELIGIOUS,), "temple"),
    "building=monastery": Feature((C.RELIGIOUS,), "monastery"),
    "building=castle": Feature((C.LANDMARK, C.ARCHITECTURE), "castle"),
    "tourism=museum": Feature((C.MUSEUM,), "museum"),
    "tourism=viewpoint": Feature((C.VIEWPOINT,), "viewpoint"),
    "tourism=artwork": Feature((C.STREET_ART,), "artwork"),
    "tourism=attraction": Feature((C.LANDMARK,), "attraction"),
    "tourism=hotel": Feature((C.HOTEL_NOTABLE,), "hotel"),
    "amenity=restaurant": Feature((C.RESTAURANT_NOTABLE,), "restaurant"),
    "leisure=park": Feature((C.PARK,), "park"),
    "leisure=garden": Feature((C.PARK,), "garden"),
    # ── noun-only (naming of abandoned/ruined things) ───────────
    "building=house": Feature((), "house"),
    "building=residential": Feature((), "residential building"),
    "building=apartments": Feature((), "apartment block"),
    "building=detached": Feature((), "house"),
    "building=farm": Feature((), "farm building"),
    "building=barn": Feature((), "barn"),
    "building=garage": Feature((), "garage"),
    "building=school": Feature((), "school"),
    "building=hospital": Feature((), "hospital"),
    "building=hotel": Feature((), "hotel"),
    "building=hangar": Feature((), "hangar"),
    "building=train_station": Feature((C.TRANSPORT,), "railway station"),
    "amenity=school": Feature((), "school"),
    "amenity=kindergarten": Feature((), "kindergarten"),
    "amenity=college": Feature((), "college"),
    "amenity=university": Feature((), "university"),
    "amenity=hospital": Feature((), "hospital"),
    "amenity=clinic": Feature((), "clinic"),
    "amenity=prison": Feature((), "prison"),
    "amenity=cinema": Feature((), "cinema"),
    "amenity=theatre": Feature((), "theatre"),
    "amenity=fuel": Feature((), "fuel station"),
    "amenity=courthouse": Feature((), "courthouse"),
    "amenity=townhall": Feature((), "town hall"),
    "amenity=community_centre": Feature((), "community centre"),
    "amenity=arts_centre": Feature((), "arts centre"),
    "amenity=swimming_pool": Feature((), "swimming pool"),
    "leisure=swimming_pool": Feature((), "swimming pool"),
    "leisure=stadium": Feature((), "stadium"),
    "leisure=sports_centre": Feature((), "sports centre"),
    "tourism=camp_site": Feature((), "camp site"),
    "place=ghost_town": Feature((C.ABANDONED,), "ghost town"),
    "place=village": Feature((), "village"),
    "place=hamlet": Feature((), "hamlet"),
    "place=isolated_dwelling": Feature((), "dwelling"),
    "landuse=cemetery": Feature((), "cemetery"),
}

# Order in which keys are consulted when choosing the *primary* feature (used for naming).
_PRIMARY_KEY_ORDER: tuple[str, ...] = (
    "military", "historic", "man_made", "natural", "railway", "aeroway", "building",
    "amenity", "tourism", "industrial", "waterway", "leisure", "landuse", "power", "place",
)

_FALSE_VALUES = frozenset({"no", "false", "0", ""})
_LIFECYCLE_KEYS = ("abandoned", "disused", "ruins")
# Tunnels that are not places to visit (drainage, passages under buildings).
_BORING_TUNNELS = frozenset({"no", "culvert", "building_passage", "flooded", "passage"})
_NOTABLE_MARKERS = frozenset({"wikipedia", "heritage", "historic", "wikidata", "stars"})
_QID_RE = re.compile(r"^Q[1-9]\d{0,11}$")
_YEAR_RE = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")
_CENTURY_RE = re.compile(r"\bC(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)[ _-]century", re.IGNORECASE)
_LANG_NAME_KEY = re.compile(r"^name:[a-z]{2,3}(?:-[A-Za-z0-9]+)?$")  # not name:signed, name:etymology
_ROAD_TYPES = frozenset({
    "residential", "tertiary", "secondary", "primary", "trunk", "motorway", "service",
    "unclassified", "living_street",
})
_NOT_IMAGE_SUFFIXES = (".html", ".htm", ".php", ".pdf", ".aspx")

# Keys that never help classification or the "tags" list shown to users.
_NOISE_KEY_PREFIXES = (
    "addr:", "name:", "source", "ref:", "contact:", "check_date", "survey:", "description",
    "note", "fixme", "created_by", "wikipedia", "wikidata", "wikimedia", "image", "mapillary",
    "opening_hours", "phone", "website", "email", "url", "fhrs:", "nycdoitt:", "gnis:",
)
_NOISE_KEYS = frozenset({
    "name", "alt_name", "old_name", "int_name", "official_name", "loc_name", "short_name",
    "type", "building", "source", "created_by", "ref", "wheelchair", "level", "layer",
})


@dataclass(frozen=True)
class OsmInterpretation:
    """Result of :func:`interpret_tags`."""

    categories: tuple[PlaceCategory, ...]
    lifecycle: str | None  # "ruins" | "abandoned" | "disused" | None
    lifecycles: tuple[str, ...]  # every lifecycle state present
    feature: str | None  # primary "key=value" (what the object is), if recognised
    noun: str | None  # human noun for the primary feature
    generated_name: str | None  # honest fallback name, e.g. "Abandoned railway station"

    @property
    def is_hidden_type(self) -> bool:
        """Abandoned / disused / ruined: the objects urban explorers look for."""
        return self.lifecycle is not None


def _is_true(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _FALSE_VALUES


def effective_tags(tags: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Resolve lifecycle prefixes: ``abandoned:railway=station`` also says ``railway=station``.

    Returns ``(tags_with_underlying_features, lifecycle_states)``; real tags win over resolved ones.
    """
    eff = dict(tags)
    states: list[str] = []

    def add(state: str) -> None:
        if state not in states:
            states.append(state)

    for key, value in tags.items():
        if not _is_true(value):
            continue
        for prefix in _LIFECYCLE_KEYS:
            if key == prefix:
                add(prefix)
            elif key.startswith(prefix + ":"):
                add(prefix)
                under = key[len(prefix) + 1 :]
                if under and ":" not in under:
                    current = eff.get(under)
                    # `railway=site` + `abandoned:railway=station`: the prefixed value says more.
                    if current is None or (
                        f"{under}={value}" in FEATURES and f"{under}={current}" not in FEATURES
                    ):
                        eff[under] = value
    if tags.get("building") in ("ruins", "collapsed") or tags.get("historic") == "ruins":
        add("ruins")
    if _is_true(tags.get("ruined")):
        add("ruins")
    if tags.get("railway") in ("abandoned", "disused"):
        add(tags["railway"])  # the value *is* the lifecycle state
    if tags.get("railway") == "disused_station":
        add("disused")
    # most "dead" → least: ruins, abandoned, disused
    ordered = [s for s in ("ruins", "abandoned", "disused") if s in states]
    return eff, ordered


def _primary_feature(eff: dict[str, str]) -> tuple[str | None, Feature | None]:
    for key in _PRIMARY_KEY_ORDER:
        value = eff.get(key)
        if value is None:
            continue
        feature = FEATURES.get(f"{key}={value}")
        if feature is not None:
            return f"{key}={value}", feature
    return None, None


def _generic_noun(eff: dict[str, str]) -> str | None:
    """Noun for things the FEATURES table does not know ("building", "industrial site"...)."""
    industrial = eff.get("industrial")
    if industrial:
        return "industrial site" if industrial in ("yes", "true") else industrial.replace("_", " ")
    if "building" in eff and eff["building"] not in _FALSE_VALUES:
        return "building"
    return None


def _build_name(lifecycle: str | None, noun: str | None) -> str | None:
    if lifecycle == "ruins":
        if noun in (None, "ruins"):
            return "Ruins"
        return f"Ruined {noun}"
    if lifecycle in ("abandoned", "disused"):
        word = "Abandoned" if lifecycle == "abandoned" else "Disused"
        return f"{word} {noun or 'place'}"
    if noun:
        return noun[0].upper() + noun[1:]
    return None


def is_street_not_place(tags: dict[str, str]) -> bool:
    """A former railway bed that is now an ordinary street (``railway=abandoned`` + ``highway=*``).

    It is a street with a history, not a place to go to. Footways/tracks/tunnels are kept.
    """
    return (
        tags.get("railway") in ("abandoned", "disused")
        and tags.get("highway") in _ROAD_TYPES
        and not tags.get("tunnel")
        and not tags.get("bridge")
    )


def interpret_tags(tags: dict[str, str]) -> OsmInterpretation:
    """Map an OSM tag dict to categories, lifecycle state and an honest generated name."""
    eff, states = effective_tags(tags)
    cats: set[PlaceCategory] = set()

    for key, value in eff.items():
        feature = FEATURES.get(f"{key}={value}")
        if feature is not None:
            cats.update(feature.categories)

    if "abandoned" in states or "disused" in states:
        cats.add(C.ABANDONED)
    if "ruins" in states:
        cats.add(C.RUINS)
    tunnel = eff.get("tunnel")
    if tunnel and _is_true(tunnel) and tunnel not in _BORING_TUNNELS:
        cats.add(C.UNDERGROUND)
    if eff.get("industrial") and _is_true(eff["industrial"]):
        cats.add(C.INDUSTRIAL)
    if eff.get("man_made") == "tower":
        tower_type = eff.get("tower:type", "")
        if tower_type == "cooling":
            cats.update((C.INDUSTRIAL,))
        elif tower_type in ("defensive", "keep", "fortified"):
            cats.update((C.MILITARY, C.LANDMARK))
        elif tower_type == "bell_tower":
            cats.update((C.RELIGIOUS, C.ARCHITECTURE))
    if eff.get("tourism") == "artwork":
        artwork_type = eff.get("artwork_type", "")
        if artwork_type in ("statue", "bust", "sculpture"):
            cats.discard(C.STREET_ART)
            cats.add(C.MONUMENT)

    # Restaurants/hotels only count when something marks them as outstanding.
    if (C.RESTAURANT_NOTABLE in cats or C.HOTEL_NOTABLE in cats) and not any(
        k in eff for k in _NOTABLE_MARKERS
    ):
        cats.discard(C.RESTAURANT_NOTABLE)
        cats.discard(C.HOTEL_NOTABLE)

    feature_key, feature = _primary_feature(eff)
    noun = feature.noun if feature else _generic_noun(eff)
    lifecycle = states[0] if states else None

    return OsmInterpretation(
        categories=tuple(sorted(cats, key=lambda c: c.value)),
        lifecycle=lifecycle,
        lifecycles=tuple(states),
        feature=feature_key,
        noun=noun,
        generated_name=_build_name(lifecycle, noun),
    )


# ── Names, links and photos ─────────────────────────────────────


def pick_name(tags: dict[str, str]) -> str | None:
    """Best display name: local name, then English, Russian, other language variants."""
    for key in ("name", "name:en", "name:ru", "int_name", "official_name", "old_name",
                "alt_name", "loc_name", "short_name"):
        value = (tags.get(key) or "").strip()
        if value:
            return value.split(";")[0].strip() or value
    for key in sorted(k for k in tags if _LANG_NAME_KEY.match(k)):
        value = (tags[key] or "").strip()
        if value:
            return value
    return None


def pick_description(tags: dict[str, str]) -> str | None:
    for key in ("description", "description:en", "description:ru", "note"):
        value = (tags.get(key) or "").strip()
        if value:
            return value
    return None


def parse_wikipedia_tag(value: str | None) -> tuple[str, str] | None:
    """``"en:Fort Mamula"`` → ``("en", "Fort Mamula")``; ``None`` if malformed."""
    if not value or ":" not in value:
        return None
    lang, title = value.split(":", 1)
    lang, title = lang.strip().lower(), title.strip()
    if not re.fullmatch(r"[a-z]{2,3}(-[a-z]+)?", lang) or not title:
        return None
    return lang, title


def wikipedia_url(lang: str, title: str) -> str:
    return f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='()_,')}"


def parse_wikidata_id(value: str | None) -> str | None:
    value = (value or "").strip().split(";")[0].strip()
    return value if _QID_RE.match(value) else None


def commons_file_url(filename: str, width: int = 640) -> str:
    """Thumbnail URL for a Commons file name (``File:`` prefix optional)."""
    name = unquote(filename.strip())
    if name.lower().startswith("file:"):
        name = name[5:]
    name = name.strip().replace(" ", "_")
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(name, safe='')}?width={width}"


_COMMONS_PAGE_RE = re.compile(
    r"^https?://(?:commons\.wikimedia\.org|[a-z]{2,3}\.wikipedia\.org)/wiki/(File:[^?#]+)", re.I
)


def extract_photos(tags: dict[str, str]) -> list[str]:
    """Photo URLs from ``image`` and ``wikimedia_commons`` tags (https only, order kept, no dups)."""
    out: list[str] = []

    def add(url: str) -> None:
        if url not in out:
            out.append(url)

    for raw in (tags.get("wikimedia_commons"), tags.get("image")):
        for part in (raw or "").split(";"):
            part = part.strip()
            if not part:
                continue
            if part.lower().startswith("file:"):
                add(commons_file_url(part))
            elif part.lower().startswith("category:"):
                continue  # a gallery, not a photo
            elif (m := _COMMONS_PAGE_RE.match(part)) is not None:
                add(commons_file_url(m.group(1)))
            elif part.lower().startswith(("https://", "http://")):
                if part.lower().split("?", 1)[0].endswith(_NOT_IMAGE_SUFFIXES):
                    continue  # a web page, not a picture
                # The bundled UI's CSP only allows https images.
                add("https://" + part.split("://", 1)[1])
    return out


def commons_category(tags: dict[str, str]) -> str | None:
    for part in (tags.get("wikimedia_commons") or "").split(";"):
        part = part.strip()
        if part.lower().startswith("category:"):
            return part
    return None


def parse_year(value: str | None) -> int | None:
    """Earliest year in an OSM ``start_date``-like value ("1914-06", "C19", "~1890s", "1900..1910")."""
    if not value:
        return None
    text = value.strip()
    if m := _YEAR_RE.search(text):
        year = int(m.group(1))
        if 100 <= year <= 2100:
            return year
    if m := _CENTURY_RE.search(text):
        century = int(m.group(1) or m.group(2))
        return (century - 1) * 100 + 50  # mid-century: C19 → 1850
    return None


_BEFORE_RE = re.compile(r"^\s*(?:before|pre|<)\b|^\s*<", re.IGNORECASE)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def describe_date(value: str | None) -> str | None:
    """Honest wording for an age: only the precision the data actually has.

    "1914-06" -> "dates from 1914", "C19" -> "dates from the 19th century",
    "before 1800" -> "dates from before 1800".
    """
    if not value or not value.strip():
        return None
    text = value.strip()
    if m := _YEAR_RE.search(text):
        year = int(m.group(1))
        if 100 <= year <= 2100:
            return f"dates from {'before ' if _BEFORE_RE.match(text) else ''}{year}"
    if m := _CENTURY_RE.search(text):
        return f"dates from the {_ordinal(int(m.group(1) or m.group(2)))} century"
    return None


# ── Tag lists for the Place.tags field ──────────────────────────


def is_noise_key(key: str) -> bool:
    return key in _NOISE_KEYS or key.startswith(_NOISE_KEY_PREFIXES)


def meaningful_tag_list(tags: dict[str, str]) -> list[str]:
    """``["key=value", ...]`` for tags that describe the object (no addresses, names, sources)."""
    return [
        f"{k}={v}" for k, v in tags.items()
        if not is_noise_key(k) and isinstance(v, str) and 0 < len(v) < 100
    ]
