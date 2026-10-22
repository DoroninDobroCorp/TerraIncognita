"""HTTP middleware: body size cap, per-IP rate limits, scanner short-circuit, security headers."""

from __future__ import annotations

import logging
import math
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.services.llm_client import budget_exhausted, seconds_until_budget_reset

logger = logging.getLogger(__name__)

MAX_REQUEST_BODY_BYTES = 64 * 1024  # 64 KB


# ── Rate limiting ────────────────────────────────────────────


@dataclass(frozen=True)
class RateRule:
    """A group of endpoints sharing one sliding-window budget (per client IP)."""

    name: str
    limit: int  # requests per window
    window: float = 60.0  # seconds
    exact: frozenset[str] = frozenset()
    prefixes: tuple[str, ...] = ()
    methods: frozenset[str] | None = None  # None → any method

    def matches(self, method: str, path: str) -> bool:
        if self.methods is not None and method not in self.methods:
            return False
        return path in self.exact or path.startswith(self.prefixes)


_POST = frozenset({"POST"})
_GET = frozenset({"GET"})

# First matching rule wins.
RATE_RULES: tuple[RateRule, ...] = (
    RateRule(
        "LLM", limit=30, methods=_POST,
        exact=frozenset({"/api/chat", "/api/describe", "/api/recommend", "/api/story",
                         "/api/story/route"}),
    ),
    RateRule(
        "discovery", limit=60, methods=_POST,
        exact=frozenset({"/api/discover", "/api/route", "/api/route/explore"}),
    ),
    RateRule("journal", limit=120, prefixes=("/api/visits", "/api/trips")),
    RateRule(
        "gamification", limit=60,
        prefixes=("/api/fog/", "/api/achievements", "/api/explorer/"),
    ),
    RateRule("offline", limit=30, methods=_POST, prefixes=("/api/offline/",)),
    RateRule("community", limit=60, prefixes=("/api/community/",)),
    RateRule(
        "daily", limit=30, methods=_GET,
        exact=frozenset({"/api/daily"}), prefixes=("/api/collections",),
    ),
)


class SlidingWindowLimiter:
    """In-memory sliding-window counter keyed by (rule, client).

    Stale keys are swept periodically so a stream of one-off client IPs
    (scanners) cannot grow the table without bound.
    """

    _SWEEP_EVERY = 300.0  # seconds

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._last_sweep = time.monotonic()

    def check(self, rule: RateRule, client: str, now: float | None = None) -> float:
        """Record a hit. Returns 0 if allowed, else seconds until the next slot frees up."""
        now = time.monotonic() if now is None else now
        self._sweep(now)
        hits = self._hits[(rule.name, client)]
        while hits and now - hits[0] >= rule.window:
            hits.popleft()
        if len(hits) >= rule.limit:
            return max(rule.window - (now - hits[0]), 0.001)
        hits.append(now)
        return 0.0

    def reset(self) -> None:
        self._hits.clear()

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < self._SWEEP_EVERY:
            return
        self._last_sweep = now
        longest = max(r.window for r in RATE_RULES)
        for key in [k for k, d in self._hits.items() if not d or now - d[-1] >= longest]:
            del self._hits[key]


limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    """Client address, honouring X-Forwarded-For only behind N trusted proxies.

    With ``trusted_proxy_hops = 1`` (nginx) the right-most entry is the address nginx
    saw; entries further left are client-supplied and can be forged.
    """
    hops = settings.trusted_proxy_hops
    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rate limits protecting the LLM budget and upstream geodata services."""

    async def dispatch(self, request: Request, call_next):
        if settings.rate_limit_enabled:
            rule = next(
                (r for r in RATE_RULES if r.matches(request.method, request.url.path)), None
            )
            if rule is not None and rule.name == "LLM" and budget_exhausted():
                return Response(
                    content='{"detail":"Daily AI budget reached, try again after 00:00 UTC"}',
                    status_code=503,
                    media_type="application/json",
                    headers={"Retry-After": str(seconds_until_budget_reset())},
                )
            if rule is not None:
                retry_after = limiter.check(rule, client_ip(request))
                if retry_after:
                    return Response(
                        content=f'{{"detail":"Rate limit exceeded for {rule.name} endpoints"}}',
                        status_code=429,
                        media_type="application/json",
                        headers={"Retry-After": str(math.ceil(retry_after))},
                    )
        return await call_next(request)


# ── Body size ────────────────────────────────────────────────


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject request bodies larger than MAX_REQUEST_BODY_BYTES."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BODY_BYTES:
            return Response(
                content='{"detail":"Request body too large"}',
                status_code=413,
                media_type="application/json",
            )
        return await call_next(request)


# ── Scanner noise ────────────────────────────────────────────

_SCANNER_PATH = re.compile(
    r"(^|/)(\.env[\w.]*|\.git|\.aws|\.ssh|wp-(admin|login|content|includes)|wp-login\.php|"
    r"xmlrpc\.php|phpinfo\.php|phpmyadmin|cgi-bin|vendor/phpunit|actuator|\.DS_Store|"
    r"config\.json|id_rsa|redirect)(/|$|\?)",
    re.IGNORECASE,
)


class ScannerBlockMiddleware(BaseHTTPMiddleware):
    """Answer well-known bot probes with a bare 404 before they reach routing or the log."""

    async def dispatch(self, request: Request, call_next):
        if settings.block_scanner_paths and _SCANNER_PATH.search(request.url.path):
            return Response(status_code=404)
        return await call_next(request)


# ── Security headers ─────────────────────────────────────────

# Hosts the bundled map UI talks to (tiles, routing, geocoding, open-data enrichment).
_CONNECT_HOSTS = (
    "https://router.project-osrm.org",
    "https://nominatim.openstreetmap.org",
    "https://demotiles.maplibre.org",
    "https://tile.openstreetmap.org",
    "https://*.tile.opentopomap.org",
    "https://server.arcgisonline.com",
    "https://tile.openhistoricalmap.org",
    "https://maps.georeferencer.com",
    "https://*.wikipedia.org",
    "https://commons.wikimedia.org",
    "https://*.wikimedia.org",
)

DEFAULT_CSP = "; ".join([
    "default-src 'self'",
    # Inline handlers are still used by the bundled UI → 'unsafe-inline' for scripts.
    "script-src 'self' 'unsafe-inline' https://unpkg.com",
    "style-src 'self' 'unsafe-inline' https://unpkg.com",
    "img-src 'self' data: blob: https:",
    "font-src 'self' data: https://unpkg.com https://demotiles.maplibre.org",
    f"connect-src 'self' {' '.join(_CONNECT_HOSTS)}",
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "frame-ancestors 'self'",
    "base-uri 'self'",
    "object-src 'none'",
])


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline browser hardening headers on every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if not settings.security_headers_enabled:
            return response
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Permissions-Policy", "geolocation=(self), microphone=(self), camera=()")
        csp = DEFAULT_CSP if settings.csp == "default" else settings.csp
        if csp and not request.url.path.startswith(("/docs", "/redoc")):
            h.setdefault("Content-Security-Policy", csp)
        return response
