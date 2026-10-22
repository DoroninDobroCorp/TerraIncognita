# Contributing

Thanks for helping make Terra Incognita better. The project is small enough that a few conventions cover everything.

## Setup

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # optional: API keys; core discovery works without any
uvicorn app.main:app --reload
```

Open <http://localhost:8000/> — the API serves the map UI from `../map`.

## Before you push

```bash
cd backend
ruff check app tests        # must be clean
pytest tests -q             # must pass; CI runs it on Python 3.11, 3.12 and 3.13
```

## Conventions

- **Tests never touch the network.** Mock HTTP with `pytest-httpx` and LLM calls with `unittest.mock` (see `tests/test_osm.py`, `tests/test_llm_api.py`). Anything that needs a real API belongs in a script, not in the suite.
- **Process-global state needs a reset.** The rate limiter, LLM usage counters and service singletons are module-level; `tests/conftest.py` resets the limiter for every test — add a fixture next to your singleton if you introduce another.
- **Additive API changes.** Add fields and options with defaults; do not rename or reshape existing responses — the UI and exported data depend on them.
- **LLM is optional.** Every LLM feature must degrade to a deterministic fallback (or a clear 503) when there is no key, the circuit breaker is open, or the daily token cap (`TERRA_LLM_DAILY_TOKEN_CAP`) is spent. Sanitize user text before it goes into a prompt (`app/utils/sanitize.py`).
- **Be polite to free services.** Overpass, Wikipedia, Nominatim and friends are shared infrastructure: send the descriptive `User-Agent`, go through the shared HTTP client and `RateLimiter`, and cache aggressively (`DiskCache`, `ttl=0` = never expires).
- **Rate limits live in one table** — `RATE_RULES` in `app/middleware.py`. A new expensive endpoint needs a rule there.

## Adding a data source

1. Subclass `BaseSource` from `app/sources/base.py` (implement `source_name` and async `search(lat, lng, radius_km)`) and return `Place` objects with `source=PlaceSource.<yours>`.
2. Register it in `app/services/discovery.py` (sources run concurrently; a failing source must never fail the request — `_safe_search` already isolates errors).
3. Add a fixture-based parser test. Fusion, classification and scoring then apply automatically.

## Adding an API endpoint

1. Pydantic request/response models in `app/models/`, logic in `app/services/`, a thin router in `app/api/`.
2. `include_router` in `app/main.py`.
3. Rate rule in `app/middleware.py` if it calls an LLM or an upstream service.
4. Tests with `TestClient` (see `tests/test_api.py`).

## Commit style

Short imperative subject (`fix: ...`, `feat: ...`, `docs: ...`), body explains *why*. One logical change per commit.
