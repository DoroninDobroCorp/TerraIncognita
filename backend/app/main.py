"""FastAPI application entry point for Terra Incognita backend."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.chat import router as chat_router
from app.api.community import router as community_router
from app.api.descriptions import router as descriptions_router
from app.api.discover import router as discover_router
from app.api.gamification import router as gamification_router
from app.api.journal import router as journal_router
from app.api.map_config import router as map_config_router
from app.api.offline import router as offline_router
from app.api.recommendations import router as recommendations_router
from app.api.routes import router as routes_router
from app.api.storytelling import router as storytelling_router
from app.services.llm_client import check_llm_health, close_llm_client, get_llm_usage
from app.utils.http_client import close_http_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

MAX_REQUEST_BODY_BYTES = 64 * 1024  # 64 KB

# Rate limiting for LLM endpoints (per-IP, sliding window)
_LLM_RATE_LIMIT = 30  # requests per window
_LLM_RATE_WINDOW = 60  # seconds
_LLM_PATHS = {"/api/chat", "/api/describe", "/api/recommend", "/api/story", "/api/story/route"}
_DISCOVERY_PATHS = {"/api/discover", "/api/route", "/api/route/explore"}
_JOURNAL_PATHS = {"/api/visits", "/api/visits/proximity", "/api/visits/dwell-check", "/api/trips"}
_GAMIFICATION_PATHS = {"/api/fog/reveal", "/api/fog/status", "/api/fog/region", "/api/fog/cells",
                       "/api/achievements", "/api/explorer/profile", "/api/explorer/xp-history",
                       "/api/explorer/leaderboard"}
_OFFLINE_PATHS = {"/api/offline/tiles/download", "/api/offline/places/cache",
                  "/api/offline/sync", "/api/offline/navigate", "/api/offline/navigate/nearest"}
_COMMUNITY_PATHS = {"/api/community/places", "/api/community/routes", "/api/community/reviews",
                    "/api/community/follow", "/api/community/karma"}
_DISCOVERY_RATE_LIMIT = 60  # requests per window
_DISCOVERY_RATE_WINDOW = 60  # seconds
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_discovery_rate_store: dict[str, list[float]] = defaultdict(list)
_journal_rate_store: dict[str, list[float]] = defaultdict(list)
_gamification_rate_store: dict[str, list[float]] = defaultdict(list)
_JOURNAL_RATE_LIMIT = 120  # requests per window
_JOURNAL_RATE_WINDOW = 60  # seconds
_GAMIFICATION_RATE_LIMIT = 60  # requests per window
_GAMIFICATION_RATE_WINDOW = 60  # seconds
_OFFLINE_RATE_LIMIT = 30  # requests per window
_OFFLINE_RATE_WINDOW = 60  # seconds
_offline_rate_store: dict[str, list[float]] = defaultdict(list)
_COMMUNITY_RATE_LIMIT = 60  # requests per window
_COMMUNITY_RATE_WINDOW = 60  # seconds
_community_rate_store: dict[str, list[float]] = defaultdict(list)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject request bodies larger than MAX_REQUEST_BODY_BYTES."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_BODY_BYTES:
            return Response(
                content='{"detail":"Request body too large"}',
                status_code=413,
                media_type="application/json",
            )
        return await call_next(request)


class LLMRateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limit LLM-powered and discovery endpoints to protect API budget."""

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()

        if request.url.path in _LLM_PATHS and request.method == "POST":
            # Clean old entries
            _rate_limit_store[client_ip] = [
                t for t in _rate_limit_store[client_ip]
                if now - t < _LLM_RATE_WINDOW
            ]

            if len(_rate_limit_store[client_ip]) >= _LLM_RATE_LIMIT:
                return Response(
                    content='{"detail":"Rate limit exceeded for LLM endpoints"}',
                    status_code=429,
                    media_type="application/json",
                )

            _rate_limit_store[client_ip].append(now)

        elif request.url.path in _DISCOVERY_PATHS and request.method == "POST":
            _discovery_rate_store[client_ip] = [
                t for t in _discovery_rate_store[client_ip]
                if now - t < _DISCOVERY_RATE_WINDOW
            ]

            if len(_discovery_rate_store[client_ip]) >= _DISCOVERY_RATE_LIMIT:
                return Response(
                    content='{"detail":"Rate limit exceeded for discovery endpoints"}',
                    status_code=429,
                    media_type="application/json",
                )

            _discovery_rate_store[client_ip].append(now)

        elif request.url.path in _JOURNAL_PATHS:
            _journal_rate_store[client_ip] = [
                t for t in _journal_rate_store[client_ip]
                if now - t < _JOURNAL_RATE_WINDOW
            ]

            if len(_journal_rate_store[client_ip]) >= _JOURNAL_RATE_LIMIT:
                return Response(
                    content='{"detail":"Rate limit exceeded for journal endpoints"}',
                    status_code=429,
                    media_type="application/json",
                )

            _journal_rate_store[client_ip].append(now)

        elif request.url.path in _GAMIFICATION_PATHS:
            _gamification_rate_store[client_ip] = [
                t for t in _gamification_rate_store[client_ip]
                if now - t < _GAMIFICATION_RATE_WINDOW
            ]

            if len(_gamification_rate_store[client_ip]) >= _GAMIFICATION_RATE_LIMIT:
                return Response(
                    content='{"detail":"Rate limit exceeded for gamification endpoints"}',
                    status_code=429,
                    media_type="application/json",
                )

            _gamification_rate_store[client_ip].append(now)

        elif request.url.path in _OFFLINE_PATHS and request.method == "POST":
            _offline_rate_store[client_ip] = [
                t for t in _offline_rate_store[client_ip]
                if now - t < _OFFLINE_RATE_WINDOW
            ]

            if len(_offline_rate_store[client_ip]) >= _OFFLINE_RATE_LIMIT:
                return Response(
                    content='{"detail":"Rate limit exceeded for offline endpoints"}',
                    status_code=429,
                    media_type="application/json",
                )

            _offline_rate_store[client_ip].append(now)

        elif any(request.url.path.startswith(p) for p in _COMMUNITY_PATHS):
            _community_rate_store[client_ip] = [
                t for t in _community_rate_store[client_ip]
                if now - t < _COMMUNITY_RATE_WINDOW
            ]

            if len(_community_rate_store[client_ip]) >= _COMMUNITY_RATE_LIMIT:
                return Response(
                    content='{"detail":"Rate limit exceeded for community endpoints"}',
                    status_code=429,
                    media_type="application/json",
                )

            _community_rate_store[client_ip].append(now)

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_http_client()
    await close_llm_client()


app = FastAPI(
    title="Terra Incognita API",
    version="0.1.0",
    description="Discovery Engine — find unusual and interesting places around you",
    lifespan=lifespan,
)

app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(LLMRateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(discover_router)
app.include_router(routes_router)
app.include_router(journal_router)
app.include_router(gamification_router)
app.include_router(chat_router)
app.include_router(descriptions_router)
app.include_router(map_config_router)
app.include_router(recommendations_router)
app.include_router(storytelling_router)
app.include_router(offline_router)
app.include_router(community_router)

# Serve map static files (explorer.html, etc.)
_project_root = Path(__file__).resolve().parent.parent.parent
_map_dir = _project_root / "map"
_data_dir = _project_root / "data"
if _map_dir.exists():
    app.mount("/map", StaticFiles(directory=str(_map_dir), html=True), name="map")
if _data_dir.exists():
    app.mount("/data", StaticFiles(directory=str(_data_dir)), name="data")


@app.get("/health")
async def health() -> dict:
    llm_status = await check_llm_health()
    llm_usage = get_llm_usage()
    return {
        "status": "ok",
        "version": "0.1.0",
        "llm": llm_status,
        "llm_usage": llm_usage,
    }
