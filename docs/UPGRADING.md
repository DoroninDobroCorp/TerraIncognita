# Upgrading

Notes for running an instance that was deployed from the earlier `main` (package version 0.1.0) and is moving to
0.2.0. A fresh install needs none of this.

## Before you start

- Stop the service and take a copy of `data/` (`deploy/backup.sh` does it). The file formats of the journal, XP,
  fog, community and offline stores are unchanged, so you can roll back by restoring that copy and the old code.
- Run **one** worker. Rate limits, the LLM budget and the stores live in the memory of the process.

## Dependencies

```bash
cd backend && pip install -e ".[dev]"      # add ".[openai]" if you use TERRA_LLM_PROVIDER=openai
```

`anthropic` is now `>=2.1,<3` (the old 0.x SDK is not supported) and Python 3.11 or newer is required.

## Settings

- **Comments in `.env`**: systemd's `EnvironmentFile=` and `docker run --env-file` keep a trailing `# comment` as
  part of the value. Put every comment on its own line (see `backend/.env.example`).
- `TERRA_LLM_EFFORT` is checked at startup: `low`, `medium`, `high`, `xhigh`, `max` or empty. Anything else stops
  the app with a clear error instead of failing at the first chat. The default model is now `claude-sonnet-5-5`;
  the Claude request carries no sampling parameters (`temperature` etc.), because current models reject them.
- **Set `TERRA_WIKIMEDIA_USER_AGENT`** to your app name and a contact. It is sent to Wikimedia, Overpass and
  Nominatim, whose policies ask for it.
- New and worth a look: `TERRA_LLM_DAILY_TOKEN_CAP` (off by default), `TERRA_TRUSTED_PROXY_HOPS` (`1` behind
  nginx — otherwise every visitor shares the proxy's rate-limit bucket), `TERRA_CORS_ORIGINS`,
  `TERRA_DISCOVERY_DEADLINE_SECONDS`, `TERRA_ATLAS_OBSCURA_ENABLED`, `TERRA_CACHE_MAX_ENTRIES`.
- Removed because nothing read them (with the `TERRA_` prefix they have no effect any more): the unused
  LLM description temperature and conversation-turn limits, the fog cell size and reveal radius, the offline
  sync retry count, the community moderation threshold and the `ROUTE_*` corridor, waypoint, duration and speed
  defaults. `backend/app/config.py` lists everything that is still read.

## Files

- **Caches are rebuilt.** The OSM and Wikidata cache keys changed (they now remember whether an answer was
  complete), so the first searches after the upgrade are slow once; old entries are ignored and evicted by
  `TERRA_CACHE_MAX_ENTRIES`. Nothing needs deleting.
- The LLM token counter lives in `cache/llm_state/usage.json`. Keep the `cache/` volume persistent if you rely
  on the daily cap across restarts.
- If a store file cannot be read, it is moved aside as `<name>.corrupt-<UTC time>` (or, when only some records
  are invalid, copied once per distinct content as `<name>.corrupt-<UTC time>-<hash>`), and the log says so. Look for such files after the first start and keep them until you
  have checked that nothing you need is in them.
- `/data` now serves `*.geojson` only. If you relied on fetching other files from it, serve them yourself.

## Behaviour that changed

- Request bodies are limited to **256 KB** (nginx: `client_max_body_size 300k;` in `deploy/nginx.conf`).
- Rate limits are one table (`RATE_RULES` in `app/middleware.py`); the journal rule now also covers `/api/notes`.
- New: `sort_by=unusual` ranks places by an explainable *unusualness score* (the `unusual_reasons` of each
  place say why); `confidence`, `distance` and `name` work as before.
- XP is now idempotent: a place pays its visit XP once, note bonuses are paid once per place and kind, and the
  streak bonus once per day. Events recorded before the upgrade carry no such key, so each place you had
  already visited can pay its visit XP one more time on the next check-in.
- A place that you marked *want to visit* and later change to *visited* now pays XP and reveals fog like a
  fresh check-in. Its visit time becomes the moment you marked it, not the day you planned it.
- Trips: a visit that an old version had moved from one trip to another was still listed in both. On the first start the
  trip lists are made to agree with each visit's own trip (no data is deleted: the visits stay, only the duplicate
  listing goes).
- Two historical layers were removed: Esri "World Topo (Historic)" (it was not a historical map) and
  OpenHistoricalMap (its raster tile host `tile.openhistoricalmap.org` no longer exists). The historical
  overlay is Old World Maps.
- `POST /api/offline/places/cache` answers `404` for an unknown `route_id` (it used to cache nothing and report
  success) and returns `source_status` and an optional `warning` when a data source fell short.
- LLM features: an unusable reply (cut off, refused, empty) is never cached; a spent daily budget makes
  chat, descriptions, stories and recommendations fall back to their plain versions and ask-the-place answer `503`.

## Docker

The image runs as the unprivileged user `terra` and keeps state in two volumes:

```bash
docker run -p 127.0.0.1:8000:8000 --env-file backend/.env \
  -v terra-data:/srv/terra/backend/data -v terra-cache:/srv/terra/backend/cache terra-incognita
```

Copy your old `data/` into the `terra-data` volume (owner `terra`). `--env-file` needs an existing file:
`cp backend/.env.example backend/.env` is enough.
