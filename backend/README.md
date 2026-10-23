# Terra Incognita — Backend (Discovery Engine)

## Quick Start

```bash
# Install
cd backend
pip install -e ".[dev]"

# Run
uvicorn app.main:app --reload

# Test
pytest tests/ -v
```

## API

### POST /api/discover

Find interesting places near a location.

```json
{
  "lat": 42.45,
  "lng": 18.53,
  "radius_km": 5.0,
  "categories": ["abandoned", "military"],
  "exclude_visited": [],
  "limit": 50,
  "sort_by": "confidence",
  "cursor": null
}
```

Response:
```json
{
  "places": [
    {
      "id": "osm_node_12345",
      "source": "osm",
      "sources": ["osm", "atlas"],
      "name": "Old Bunker",
      "description": "A WWII military bunker",
      "categories": ["military", "underground"],
      "coordinates": {"lat": 42.45, "lng": 18.53},
      "confidence": 0.85,
      "distance_m": 120.5,
      "tags": ["military=bunker"],
      "photos": ["https://..."],
      "metadata": {}
    }
  ],
  "total": 42,
  "has_more": true,
  "cursor": "eyI..."
}
```

### GET /health

Health check endpoint.

## Configuration

Set via environment variables (prefix `TERRA_`) or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `TERRA_OVERPASS_URL` | `https://overpass-api.de/api/interpreter` | Overpass API endpoint |
| `TERRA_CACHE_DIR` | `cache` | Cache directory path |
| `TERRA_CACHE_TTL_SECONDS` | `86400` | Cache TTL (24h) |
| `TERRA_ANTHROPIC_API_KEY` | — | For LLM classification (optional) |
| `TERRA_DEFAULT_RADIUS_KM` | `5.0` | Default search radius |
| `TERRA_MAX_RADIUS_KM` | `50.0` | Maximum allowed radius |

## Architecture

```
app/
├── main.py           # FastAPI app
├── config.py         # Settings
├── models/place.py   # Place, PlaceCategory, API models
├── sources/          # Data source adapters
│   ├── base.py       # Abstract base
│   ├── osm.py        # OpenStreetMap/Overpass
│   ├── atlas_obscura.py  # Atlas Obscura
│   └── wikidata.py   # Wikidata SPARQL
├── services/
│   ├── discovery.py  # Orchestrator
│   ├── fusion.py     # Deduplication & merge
│   ├── classifier.py # Category classification
│   └── cache.py      # Disk cache
└── utils/
    ├── geo.py        # Haversine, bounding box
    └── rate_limiter.py
```

## Docker

```bash
docker build -t terra-backend .
docker run -p 8000:8000 terra-backend
```

## Search quality & enrichment

How a search turns raw map data into "3 genuinely unusual places nearby":

1. **Sources** (queried concurrently, each with its own time budget): OpenStreetMap/Overpass,
   Wikidata, Atlas Obscura, Deep Research. A failing source never fails the request; the response
   says what happened in `source_status` (`{"osm": "ok", "atlas": "error", ...}` with
   `ok | empty | error | timeout`).
2. **OSM** (`sources/osm.py`, `sources/osm_tags.py`): two Overpass queries run concurrently — the
   *gems* tier (abandoned / disused / ruined objects and `abandoned:*` / `disused:*` prefixes,
   historic ruins, forts, mines, wrecks; military bunkers; adits, water towers, silos, tunnels,
   caves; dead railway stations; industrial heritage) and, up to 15 km radius, a *landmarks* tier
   (museums, memorials, viewpoints ...). Unnamed objects are kept with an honest generated name
   ("Abandoned railway station", `metadata.name_generated = true`) and rank lower. Parsed into
   `metadata`: `name_en`, `name_ru`, `wikidata_id`, `wikipedia_url`, `start_date`,
   `historic_period`, `access`, `osm_url`, `lifecycle`, `osm_feature`, ...; Commons/`image` tags
   become `photos`. Mirrors: `TERRA_OVERPASS_FALLBACK_URLS`.
3. **Fusion** (`services/fusion.py`): two entries are one place only if distance *and* identity
   agree (shared Wikidata id, matching distinctive names in any script/language up to
   `TERRA_DEDUP_NAME_MATCH_DISTANCE_METERS` apart, or unnamed places from different sources
   within `TERRA_DEDUP_DISTANCE_METERS` with compatible categories). Two bunkers 30 m apart stay
   two places. Merging keeps all photos, tags, sources, categories, deep-merged metadata,
   `merged_ids` / `alt_names`, and the highest unusual score.
4. **Scoring** (`services/scoring.py`): every place gets `unusual_score` (0..1) and
   `unusual_reasons` (e.g. `["military bunker", "abandoned", "no Wikipedia article — off the
   tourist path"]`). Deterministic and rule-based: category rarity, Atlas Obscura listing, OSM
   lifecycle/bunker/mine/cave tags, age, restricted access, a small data-richness bonus; penalties
   for famous (Wikipedia language editions via Wikidata `sitelinks`), ticketed, museum,
   `tourism=attraction`, ordinary hilltops and unnamed places.
5. **Sorting**: `sort_by` is `confidence` (default) | `distance` | `name` | `unusual`. `unusual`
   orders by score and keeps parts of one site (a fort's eleven ruined buildings, the segments of
   one abandoned railway) from crowding the top — nothing is dropped, siblings just sink.

### POST /api/place/enrich

```json
{"name": "Fort Mamula", "lat": 42.3953, "lng": 18.5583, "wikidata_id": "Q260715", "lang": "ru"}
```
```json
{
  "wikipedia": {"title": "Mamula", "extract": "…", "url": "https://ru.wikipedia.org/wiki/…", "lang": "ru"},
  "photos": [{"url": "…", "thumb_url": "…", "title": "…", "author": "…", "license": "CC BY-SA 4.0",
              "source_url": "https://commons.wikimedia.org/wiki/File:…"}],
  "sources": ["wikipedia", "commons"]
}
```
The article comes from the Wikidata sitelink (if `wikidata_id` is given) or a Wikipedia geosearch
that must match the name; `lang` is tried first, then `en`. Photos are Commons files near the point
(plus the article's lead image), junk (logos, maps, flags, SVG) dropped, max 6, with the licence
and author attribution they require. Wikipedia or Commons being down degrades the answer instead of
failing it (never a 500). Results are cached on disk forever per rounded coordinates + name +
language (outage-affected answers are not cached). Rate limit: 30 requests/minute per client.
Settings: `TERRA_WIKIMEDIA_USER_AGENT` (set your own contact!), `TERRA_ENRICHMENT_*`.

### Golden-set quality tests

`tests/fixtures/overpass_*.json` hold Overpass-style element lists for three very different areas
(a Montenegrin coast recorded from Overpass, an industrial town and a Cyrillic-named port city,
both synthetic) with hand-labelled `gems` and `clutter`. `tests/test_golden_search.py` runs them
through the real pipeline and asserts *ranks* (all gems in the top N, no clutter there, every gem
above every clutter item ...) — the regression harness for any scoring/fusion tuning. A new fixture
file is picked up automatically:

```bash
cd backend
PYTHONPATH=. python -m tests.golden record my_city 50.06 19.94 3     # one polite Overpass request
PYTHONPATH=. python -m tests.golden rank tests/fixtures/overpass_my_city.json
# label gems/clutter in the JSON ("expect" block), then: pytest tests/test_golden_search.py
```

### Known limitations

* Atlas Obscura answers API clients with HTTP 403 (bot protection), so its "listed on Atlas
  Obscura" signal only fires when data gets through; the source backs off for 10 minutes after a
  failure instead of retrying on every search.
* Overpass is a shared free service; a 4 km search is typically a few seconds, large radii
  (> 15 km) skip the landmarks tier, and the element cap is `TERRA_OVERPASS_MAX_ELEMENTS`.
* Generated names are English ("Abandoned building"); clients can localise them from
  `metadata.name_generated`, `metadata.lifecycle` and `metadata.osm_feature`.
