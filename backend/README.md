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

## Daily place, loops, collections, streak, ask-the-place

Features meant to give the app a reason to be opened every day. All of them are optional
layers over the existing discovery, route and journal code; the LLM ones degrade gracefully
(see [LLM behaviour](#llm-behaviour)).

| Endpoint | Purpose | Rate limit group |
|----------|---------|------------------|
| `GET /api/daily` | Place of the day for the neighbourhood | `daily` (30/min) |
| `POST /api/route/loop` | Round trip from a point within N hours | `discovery` (60/min) |
| `GET /api/collections` | "10/47 abandoned places" progress per category | `daily` (30/min) |
| `GET /api/streak` | Consecutive days with a visit | none |
| `POST /api/place/ask` | Ask a question about one place | `LLM` (30/min) |
| `POST /api/story/walk` | Short essay about a finished walk | `LLM` (30/min) |

### GET /api/daily

`GET /api/daily?lat=42.46&lng=18.53&radius_km=10&lang=en`

```json
{
  "date": "2026-10-22",
  "place": { "id": "...", "name": "Fort Mamula", "coordinates": {"lat": 42.39, "lng": 18.56}, "distance_m": 3480.2, "...": "..." },
  "reason": "An unusual ruins spot, 3.5 km from you — well documented.",
  "story": "A fortress rises from the middle of the bay ... (3-5 sentences) or null"
}
```

- **Deterministic.** The pick is a function of (UTC date, ~0.05 degree grid cell, radius).
  Discovery runs around the *centre of the cell* (about 5 km wide), so everyone in the
  neighbourhood sees the same place all day; `place.distance_m` is measured from the caller.
  Consequently the place can be up to roughly `radius_km` + 4 km from the exact request point.
- **Ranking** lives in one function, `_rank_key` in `services/daily_place.py`: unusual places,
  confidence, description, photos, and `unusual_score` / `wikipedia` when the `Place` model has
  them. The day's place is taken from the top `TERRA_DAILY_TOP_K` (default 5) by rotation, so
  it changes every day and never repeats two days in a row while the top K is stable.
- The pick is cached for the day per (date, cell, radius), so a flaky data source cannot make
  it flip and repeat calls are cheap.
- **Radius widens** automatically: requested radius, then 25 km, then 50 km, never above
  `TERRA_MAX_RADIUS_KM`. Nothing found (unnamed places do not count) gives `404` with a hint to
  try another location, and the LLM is not called.
- `reason` is a deterministic sentence (English, or Russian when `lang=ru`).
- `story` is a 3-5 sentence blurb in `lang` (an unsupported language gives English), cached
  forever per (place id, language); `null` whenever the LLM cannot be used.

### POST /api/route/loop

```json
{ "lat": 42.45, "lng": 18.53, "duration_hours": 3, "mode": "walking",
  "categories": [], "max_stops": 5, "exclude_visited": [] }
```

`duration_hours` 0.5-12, `mode` `walking` or `cycling`, `max_stops` 2-8; `categories` and
`exclude_visited` are optional. The response is the same `RouteResponse` as `POST /api/route`
(`is_circular: true`, first waypoint = start, last waypoint = "Return to Start" at the same
coordinates), so `POST /api/route/export` produces GPX/KML for it unchanged.

- **Time budget:** travel time + `TERRA_ROUTE_VISIT_TIME_MINUTES` per stop must fit
  `duration_hours * (1 - TERRA_LOOP_SAFETY_MARGIN)` (margin default 0.10). Travel time uses
  `TERRA_ROUTE_WALKING_SPEED_KMH` / `TERRA_ROUTE_CYCLING_SPEED_KMH`; segment durations in the
  response use the same speed. Distances are straight lines, like every other route here; the
  margin is what absorbs real-path detours. `route.metadata` carries the time breakdown
  (`travel_time_s`, `visit_time_s`, `total_time_s`, `usable_budget_s`).
- **Selection:** candidates come from one discovery call (radius about speed x time / 3,
  widened once in sparse areas). The compass around the start is split into `max_stops`
  sectors and the best place of each is taken (quality discounted by distance), ordered with
  the existing NN + 2-opt helper (closing leg included) or an angular sweep, whichever is
  shorter, then trimmed until it fits and filled with extra stops while time remains.
- `404` when fewer than two places are reachable, `422` for invalid parameters or when the
  duration cannot fit two visits at the configured visit time.

### GET /api/collections

`GET /api/collections?lat=42.4&lng=18.5&radius_km=50`

```json
{ "collections": [
  { "category": "abandoned", "label": "Abandoned", "icon": "🏚️", "visited": 3, "total": 12 }
] }
```

Biggest collections first; categories with nothing nearby are omitted. A visit counts toward a
place by id, otherwise by proximity (`TERRA_JOURNAL_PROXIMITY_RADIUS_M`) plus a compatible
name; each visit claims at most one place. Only `visited` journal entries count. A place counts
in every category it has. Totals come from one discovery page (the 200 best-ranked places), so
in very dense areas they are a lower bound.

### GET /api/streak

`GET /api/streak?tz_offset_minutes=180`

```json
{ "current": 4, "longest": 9, "last_active": "2026-10-22", "active_today": true,
  "days": ["2026-09-01", "...", "2026-10-22"] }
```

Consecutive local days with at least one visited place. `tz_offset_minutes` is the viewer's
offset **east** of UTC (UTC+3 is `180`; in JavaScript pass `-new Date().getTimezoneOffset()`),
range +/-840. A streak is alive while the last active day is today or yesterday. `days` are
the active days of the last 60, oldest first. Notes do not count (the journal API only offers
per-visit note lookup, which is too slow for a home-screen call).

### POST /api/place/ask

```json
{ "place": { "name": "Fort Mamula", "lat": 42.39, "lng": 18.56,
             "categories": ["military"], "description": "..." },
  "question": "Is it worth going in the rain?", "language": "auto" }
```
Response: `{ "answer": "..." }`.

- `question` is limited to 500 characters (`422` above); `description` is cut to 2000 and
  `name` to 200 characters; `language` is `auto` (answer in the question's language) or a
  two-letter code.
- The answer is grounded in the supplied facts, admits what it does not know instead of
  inventing it, and ignores instructions hidden in the place fields or the question. Place
  text is cleaned (single line, no control or invisible characters, no angle brackets,
  injection markers neutralised) and only ever appears in the user message inside delimited
  blocks; the system prompt contains no user text. Output is capped at
  `TERRA_PLACE_ASK_MAX_TOKENS` tokens (default 500).
- Identical (place, question, language) requests are answered from a disk cache for
  `TERRA_PLACE_ASK_CACHE_TTL_SECONDS` (7 days), even when the LLM is down.
- `503` with a clear message when the LLM is unavailable (no key, circuit breaker open,
  provider failure, or the daily budget is spent: the rate-limit middleware answers that one
  with `Retry-After`).

### POST /api/story/walk

```json
{ "places": [ { "name": "Old Mill", "lat": 42.4, "lng": 18.5, "categories": ["industrial"] } ],
  "total_distance_km": 7.4, "duration_minutes": 185, "language": "en" }
```
Response: `{ "report": "...", "ai_generated": true }`. Up to 20 places, same prompt hygiene as
ask-the-place, cached forever per walk and language. When the LLM is unavailable the response
is a plain deterministic summary with `ai_generated: false` rather than an error.

### LLM behaviour

| Situation | `/api/daily` | `/api/place/ask` | `/api/story/walk` |
|-----------|--------------|------------------|-------------------|
| Cached result exists | served | served | served |
| No API key / circuit open / provider error | `story: null` | `503` | plain summary |
| Daily token budget spent | `story: null` | `503` (middleware) | `503` (middleware) |

Concurrent identical requests share a single LLM call. Provider error text is logged by type
only and never returned. Caches live under `TERRA_CACHE_DIR`: `daily_pick/` (2 days),
`daily_story/` (forever), `place_ask/` (7 days), `walk_report/` (forever).

### Settings added

| Variable | Default | Description |
|----------|---------|-------------|
| `TERRA_DAILY_TOP_K` | `5` | Size of the shortlist the day's place rotates through |
| `TERRA_DAILY_STORY_MAX_TOKENS` | `400` | Output cap for the daily story |
| `TERRA_LOOP_SAFETY_MARGIN` | `0.10` | Share of a loop's time budget kept in reserve |
| `TERRA_PLACE_ASK_MAX_TOKENS` | `500` | Output cap for ask-the-place answers |
| `TERRA_PLACE_ASK_CACHE_TTL_SECONDS` | `604800` | How long answers are cached |
| `TERRA_WALK_REPORT_MAX_TOKENS` | `600` | Output cap for walk reports |

Tests for these features live in `tests/features/` and never touch the network or a real LLM.
