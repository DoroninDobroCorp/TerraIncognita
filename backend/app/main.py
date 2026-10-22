"""FastAPI application entry point for Terra Incognita backend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as chat_router
from app.api.community import router as community_router
from app.api.descriptions import router as descriptions_router
from app.api.discover import router as discover_router
from app.api.gamification import router as gamification_router
from app.api.journal import router as journal_router
from app.api.map_config import router as map_config_router
from app.api.offline import router as offline_router
from app.api.place_collections import router as place_collections_router
from app.api.recommendations import router as recommendations_router
from app.api.routes import router as routes_router
from app.api.storytelling import router as storytelling_router
from app.api.streak import router as streak_router
from app.config import settings
from app.middleware import (
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
    ScannerBlockMiddleware,
    SecurityHeadersMiddleware,
)
from app.services.llm_client import check_llm_health, close_llm_client, get_llm_usage
from app.utils.http_client import close_http_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_http_client()
    await close_llm_client()


VERSION = "0.2.0"

app = FastAPI(
    title="Terra Incognita API",
    version=VERSION,
    description="Discovery Engine — find unusual and interesting places around you",
    lifespan=lifespan,
)

_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
_cors_open = "*" in _cors_origins

# The last middleware added is the outermost one.
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_open else _cors_origins,
    allow_credentials=not _cors_open,  # credentials + wildcard origin is unsafe (and invalid)
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ScannerBlockMiddleware)

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
app.include_router(place_collections_router)
app.include_router(streak_router)

# Serve map static files (explorer.html, etc.)
_project_root = Path(__file__).resolve().parent.parent.parent
_map_dir = _project_root / "map"
_data_dir = _project_root / "data"
if _map_dir.exists():
    app.mount("/map", StaticFiles(directory=str(_map_dir), html=True), name="map")
if _data_dir.exists():
    app.mount("/data", StaticFiles(directory=str(_data_dir)), name="data")


@app.get("/")
async def root():
    return RedirectResponse(url="/map/app.html")


@app.get("/health")
async def health() -> dict:
    """Liveness + LLM configuration. Never calls the LLM, so it is free to poll."""
    llm_status = await check_llm_health()
    llm_usage = get_llm_usage()
    return {
        "status": "ok",
        "version": VERSION,
        "llm": llm_status,
        "llm_usage": llm_usage,
    }


@app.get("/version")
async def version() -> dict:
    return {"name": "terra-incognita", "version": VERSION}


@app.get("/robots.txt", include_in_schema=False)
async def robots() -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /api/\nAllow: /\n")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    icon = _map_dir / "favicon.svg"
    if icon.exists():
        return FileResponse(icon, media_type="image/svg+xml")
    return PlainTextResponse("", status_code=204)
