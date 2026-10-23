# Architecture

Terra Incognita is one FastAPI process that serves a JSON API and a static, build-free map UI.
There is no database: user data is JSON files, caches are JSON files, and rate limits live in memory.

```
 browser (map/, PWA)  ──HTTP──▶  middleware ──▶ routers (app/api) ──▶ services ──▶ sources ──▶ Overpass,
                                                                         │                      Wikidata,
                                                                         ├──▶ llm_client ──▶ Anthropic/OpenAI   Wikipedia, Commons,
                                                                         └──▶ data/, cache/ (JSON files)        Atlas Obscura, Gemini
```

## Request path

Middleware, outermost first (`app/main.py`, `app/middleware.py`):

1. **ScannerBlock** — bot probes (`/.env`, `/wp-login.php`, …) get a bare 404 before anything else runs.
2. **CORS** — origins from `TERRA_CORS_ORIGINS`; credentials only with explicit origins.
3. **SecurityHeaders** — `nosniff`, frame/referrer/permissions policy and a CSP (`DEFAULT_CSP`).
4. **RateLimit** — one table, `RATE_RULES`; per client IP, sliding window, `Retry-After` on 429. Behind a
   reverse proxy set `TERRA_TRUSTED_PROXY_HOPS` or every user shares the proxy's address. LLM endpoints also
   answer 503 when the daily token budget is spent.
5. **RequestSizeLimit** — 64 KB, counted on the bytes actually received (chunked uploads included).

Request validation errors go through one global handler that never echoes raw input (a `NaN` in a body would
otherwise make FastAPI fail to serialise its own 422).

## Discovery pipeline (`app/services/discovery.py`)

```
OSM · Wikidata · Atlas Obscura · Deep Research   (concurrent, each isolated + time-boxed)
        │
        ▼  answer at discovery_deadline_seconds (25 s) once anything has arrived;
        │  stragglers are reported as "timeout" and keep running to warm the disk cache
   fuse_places        merge duplicates only if distance AND identity agree (shared Wikidata id,
        │             matching names in any script, or compatible unnamed objects); lose no data
   classify_places    rule-based categories (whole-word, multilingual stems)
   recompute_confidence
   apply_unusual_scores   unusual_score 0..1 + human-readable unusual_reasons (services/scoring.py)
        │
   filter → distance → sort (confidence | distance | name | unusual) → cursor pagination
```

- **Sources** (`app/sources/`) implement `BaseSource.search(lat, lng, radius_km)`; a failing source yields
  `source_status[name] = "error"` and never fails the request. Failures are *not* cached.
- **Scoring** is deterministic and rule-based: category rarity, Atlas Obscura listing, abandonment/age/access
  tags, minus mainstream signals (many Wikipedia editions, tourist attraction, museums). `sort_by="unusual"`
  also stops parts of one site from crowding the top. Tune it against `tests/test_golden_search.py`.
- **Enrichment** (`POST /api/place/enrich`) is lazy and per place: Wikipedia summary + Commons photos with
  attribution, cached forever.

## Feature modules

| Feature | Endpoint(s) | Module |
|---|---|---|
| Place of the day | `GET /api/daily` | `services/daily_place.py` — deterministic per (UTC date, ~5 km cell) |
| Loop route | `POST /api/route/loop` | `services/loop_route.py` — sector selection + 2-opt, fits a time budget |
| Collections | `GET /api/collections` | `services/place_collections.py` |
| Streak | `GET /api/streak` | `services/streak.py` — local-day streak from journal visits |
| Ask a place | `POST /api/place/ask` | `services/place_ask.py` |
| Walk essay | `POST /api/story/walk` | `services/walk_report.py` |
| Routes, journal, fog/XP, community, offline | see `backend/README.md` | `services/{route_*,journal,gamification,community,offline_manager}.py` |

## LLM layer

`services/llm_client.py` talks to Anthropic (default) or OpenAI, with a circuit breaker, a disk cache for
generated text, and a persisted **daily token cap** (`TERRA_LLM_DAILY_TOKEN_CAP`). Every LLM feature is optional:
no key, open circuit or spent budget means a deterministic fallback or a clear 503. Untrusted text (place
names, tags, questions) is cleaned and fenced before it reaches a prompt (`utils/sanitize.py`,
`services/llm_support.py`); model output is validated, never trusted.

## Persistence

| What | Where | Notes |
|---|---|---|
| Journal, gamification, community, offline | `data/<store>/*.json` (`TERRA_*_DATA_DIR`) | atomic writes (`utils/atomic_io.py`); an unreadable file is preserved as `*.corrupt-<utc>`, never overwritten |
| HTTP/LLM caches | `cache/` (`TERRA_CACHE_DIR`) | `DiskCache`: TTL (0 = forever), atomic, bounded by `TERRA_CACHE_MAX_ENTRIES` |
| LLM budget counter | `cache/llm_usage.json` | survives restarts |

State is held in memory per process, so **run a single worker** (the Docker image and the systemd unit do).
`deploy/backup.sh` snapshots `data/`.

## Frontend (`map/`)

Plain static files, classic scripts, no build step — see `map/README.md`. Highlights: `app.html` has no inline
script or handlers (strict `script-src`), all dynamic markup goes through an escaping `html` template tag,
`sw.js` makes it an installable PWA with an offline shell and a bounded tile cache.

## Known limits (deliberate, documented)

- **Single explorer.** Journal, XP/fog and offline state are global to the instance. Community endpoints take
  a client-supplied `author_id`/`user_id`, so reputation can be faked. Going multi-user needs real
  authentication and a user dimension in every store — the biggest architectural step still open.
- **Presence can't be verified**, so fog/XP can be farmed by a client that fakes GPS or invents place ids.
- JSON files rewrite whole documents on save; fine for one person, not for many.
- Overpass and Atlas Obscura are external and flaky; Atlas blocks most server-side traffic.
