"""Shared HTTP client with connection pooling."""

from __future__ import annotations

import httpx

from app.config import settings

# Re-usable async client with connection pooling.
# Created lazily on first import; closed on application shutdown via lifespan.
_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"User-Agent": settings.wikimedia_user_agent},  # also used for Overpass and Nominatim
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
