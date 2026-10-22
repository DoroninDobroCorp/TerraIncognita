"""Place enrichment: a Wikipedia summary and freely-licensed photos with attribution.

Wikipedia
    The article is found through the Wikidata sitelink when ``wikidata_id`` is known, otherwise
    through a geosearch around the coordinates that prefers a name match. The requested language
    is tried first, then English. Summaries come from the REST API; disambiguation pages and
    pages without an extract are skipped.
Wikimedia Commons
    A geosearch of files near the point (plus the article's lead image, which is usually the best
    picture of the place). Only real photos are kept (no SVG logos, maps, flags ...), each with
    the author and licence its licence requires.

Every outgoing call goes through one rate limiter with a Wikimedia-compliant User-Agent.
Failures of one provider degrade gracefully (the other still answers) and are never cached;
complete results are cached on disk forever (``DiskCache(ttl=0)``) because they are keyed by
rounded coordinates + name + language and Wikipedia text for a place barely changes.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from app.config import settings
from app.models.place import EnrichedPhoto, EnrichRequest, EnrichResponse, WikipediaInfo
from app.services import names
from app.services.cache import DiskCache
from app.utils.http_client import get_http_client
from app.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Results are cached forever (ttl=0): the key is rounded coordinates + name + language.
enrichment_cache = DiskCache(ttl=0)
_CACHE_VERSION = "v1"

_limiter = RateLimiter(
    max_requests=settings.enrichment_rate_limit_requests,
    window_seconds=settings.enrichment_rate_limit_window_seconds,
)
_inflight: dict[str, asyncio.Future[EnrichResponse]] = {}

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA_API = "https://{lang}.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"

_GEOSEARCH_RADIUS_M = 1000
_NAME_MATCH_MIN = 0.5  # token overlap needed to accept a geosearch hit for a named place
_SAME_SPOT_M = 15.0  # a hit this close is accepted even when the names do not match
_UNNAMED_MAX_M = 150.0  # …and for unnamed/generic places the nearest hit within this distance
_EXTRACT_MAX_CHARS = 1200
_MIN_PHOTO_WIDTH = 400
_PHOTO_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_JUNK_TITLE = re.compile(
    r"\b(logo|icon|flag|coat[ _]of[ _]arms|blason|wappen|escudo|map|locator|diagram|plan|schema|"
    r"scheme|chart|banner|stamp|signature|seal|emblem|symbol|graph|table|screenshot|poster|"
    r"coin|banknote)\b",
    re.IGNORECASE,
)
_BAD_SUMMARY_TYPES = frozenset({"disambiguation", "mainpage", "no-extract", "not_found"})
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


# ── Public entry point ─────────────────────────────────────────


def cache_key(req: EnrichRequest) -> str:
    """Rounded coordinates (~11 m) + name + language: nearby clicks on one place share a result."""
    name = " ".join(names.tokens(req.name))
    return (
        f"enrich:{_CACHE_VERSION}:{req.lang}:{req.lat:.4f}:{req.lng:.4f}:"
        f"{req.wikidata_id or ''}:{name}"
    )


async def enrich_place(req: EnrichRequest) -> EnrichResponse:
    """Wikipedia summary + Commons photos for a place. Never raises for provider failures."""
    key = cache_key(req)
    cached = await enrichment_cache.get(key)
    if cached is not None:
        try:
            return EnrichResponse.model_validate(cached)
        except ValueError:
            logger.warning("Ignoring unreadable enrichment cache entry %s", key)

    # Identical concurrent requests share one set of outgoing calls.
    future = _inflight.get(key)
    if future is None:
        future = asyncio.ensure_future(_compute(key, req))
        _inflight[key] = future
        future.add_done_callback(lambda _f, k=key: _inflight.pop(k, None))
    return await asyncio.shield(future)


async def _compute(key: str, req: EnrichRequest) -> EnrichResponse:
    wiki, commons = await asyncio.gather(
        _guard("wikipedia", _find_wikipedia(req)),
        _guard("commons", _find_commons_photos(req)),
    )
    found: _WikiResult = wiki.value or _WikiResult()
    photos: list[EnrichedPhoto] = commons.value or []
    failed = wiki.failed or commons.failed or found.degraded

    if found.lead_file:
        lead = await _guard("commons", _lead_image(found.lead_file))
        failed = failed or lead.failed
        if lead.value is not None:
            photos = [lead.value, *(p for p in photos if p.source_url != lead.value.source_url)]
    photos = photos[: settings.enrichment_max_photos]

    sources = []
    if found.info is not None:
        sources.append("wikipedia")
    if photos:
        sources.append("commons")
    response = EnrichResponse(wikipedia=found.info, photos=photos, sources=sources)

    if not failed:  # a half-answer caused by an outage must not be remembered forever
        await enrichment_cache.set(key, response.model_dump(mode="json"))
    return response


class _Guarded:
    __slots__ = ("failed", "value")

    def __init__(self, value: Any = None, failed: bool = False) -> None:
        self.value = value
        self.failed = failed


async def _guard(provider: str, coro) -> _Guarded:
    try:
        return _Guarded(await coro)
    except Exception as exc:  # any provider failure degrades, never raises
        logger.warning("Enrichment provider %s failed: %s: %s", provider, type(exc).__name__, exc)
        return _Guarded(failed=True)


# ── HTTP ───────────────────────────────────────────────────────


async def _get_json(url: str, params: dict[str, Any] | None = None) -> Any | None:
    """GET JSON through the rate limiter. ``None`` on 404; other failures raise."""
    await _limiter.acquire()
    resp = await get_http_client().get(
        url,
        params=params,
        headers={"User-Agent": settings.wikimedia_user_agent, "Accept": "application/json"},
        timeout=settings.wikimedia_timeout,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


# ── Wikipedia ──────────────────────────────────────────────────


@dataclass
class _WikiResult:
    info: WikipediaInfo | None = None
    lead_file: str | None = None  # Commons file name of the article's lead image
    degraded: bool = False  # a lookup failed on the way: the answer may be incomplete


async def _find_wikipedia(req: EnrichRequest) -> _WikiResult:
    """The best article for the place, trying (in order) the Wikidata sitelink and then a
    geosearch, in the requested language and then in English."""
    langs = [req.lang] if req.lang == "en" else [req.lang, "en"]
    result = _WikiResult()

    sitelinks: dict[str, str] = {}
    if req.wikidata_id:
        try:
            sitelinks = await _wikidata_sitelinks(req.wikidata_id, langs)
        except Exception as exc:  # fall back to geosearch, but do not cache the outcome
            logger.warning("Wikidata sitelink lookup failed: %s", exc)
            result.degraded = True

    attempts = [("sitelink", lang) for lang in langs if lang in sitelinks]
    attempts += [("geosearch", lang) for lang in langs]
    for method, lang in attempts:
        try:
            title = sitelinks[lang] if method == "sitelink" else await _geosearch_title(lang, req)
            found = await _wikipedia_summary(lang, title) if title else None
        except Exception as exc:  # one language failing must not lose the other
            logger.warning("Wikipedia %s lookup (%s) failed: %s", method, lang, exc)
            result.degraded = True
            continue
        if found is not None:
            result.info, result.lead_file = found
            return result
    return result


async def _wikidata_sitelinks(qid: str, langs: list[str]) -> dict[str, str]:
    """``{lang: article title}`` for the languages that have an article for this item."""
    data = await _get_json(WIKIDATA_API, {
        "action": "wbgetentities", "ids": qid, "props": "sitelinks",
        "sitefilter": "|".join(f"{lang}wiki" for lang in langs), "format": "json",
    })
    entity = ((data or {}).get("entities") or {}).get(qid) or {}
    links = entity.get("sitelinks") or {}
    out: dict[str, str] = {}
    for lang in langs:
        title = (links.get(f"{lang}wiki") or {}).get("title")
        if isinstance(title, str) and title:
            out[lang] = title
    return out


async def _geosearch_title(lang: str, req: EnrichRequest) -> str | None:
    data = await _get_json(WIKIPEDIA_API.format(lang=lang), {
        "action": "query", "list": "geosearch", "gscoord": f"{req.lat}|{req.lng}",
        "gsradius": _GEOSEARCH_RADIUS_M, "gslimit": 10, "format": "json", "formatversion": 2,
    })
    hits = ((data or {}).get("query") or {}).get("geosearch") or []
    return pick_geosearch_title(hits, req.name)


def pick_geosearch_title(hits: list[dict], name: str | None) -> str | None:
    """Choose the article about *this* place among nearby ones.

    A real name must match (accents/scripts ignored); without one — or with a generic one like
    "Bunker" — only a very close hit is trusted, because the church next door is not the bunker.
    """
    candidates = [h for h in hits if isinstance(h, dict) and isinstance(h.get("title"), str)]
    if not candidates:
        return None

    def distance(h: dict) -> float:
        d = h.get("dist")
        return float(d) if isinstance(d, int | float) else float("inf")

    if name and not names.is_generic_name(name):
        wanted = names.distinctive_tokens(name)

        def plain_title(h: dict) -> str:
            return re.sub(r"\s*\([^)]*\)\s*$", "", h["title"])  # "Mamula (island)" → "Mamula"

        def similarity(h: dict) -> float:
            return names.name_similarity(name, plain_title(h))

        def tightness(h: dict) -> float:
            """Jaccard of the token sets: "Mamula" fits the query better than "Hotel Mamula Palace"."""
            got = names.distinctive_tokens(plain_title(h))
            return len(wanted & got) / len(wanted | got) if wanted | got else 0.0

        best = max(candidates, key=lambda h: (similarity(h), tightness(h), -distance(h)))
        if similarity(best) >= _NAME_MATCH_MIN:
            return best["title"]
        nearest = min(candidates, key=distance)
        return nearest["title"] if distance(nearest) <= _SAME_SPOT_M else None

    nearest = min(candidates, key=distance)
    return nearest["title"] if distance(nearest) <= _UNNAMED_MAX_M else None


async def _wikipedia_summary(lang: str, title: str) -> tuple[WikipediaInfo, str | None] | None:
    url = WIKIPEDIA_SUMMARY.format(lang=lang, title=quote(title.replace(" ", "_"), safe=""))
    data = await _get_json(url)
    if not isinstance(data, dict):
        return None
    extract = (data.get("extract") or "").strip()
    if data.get("type") in _BAD_SUMMARY_TYPES or not extract:
        return None
    page_title = data.get("title") or title
    page_url = ((data.get("content_urls") or {}).get("desktop") or {}).get("page") or (
        f"https://{lang}.wikipedia.org/wiki/{quote(page_title.replace(' ', '_'), safe='()_,')}"
    )
    info = WikipediaInfo(
        title=page_title,
        extract=_shorten(extract, _EXTRACT_MAX_CHARS),
        url=page_url,
        lang=data.get("lang") or lang,
    )
    return info, _commons_file_name((data.get("originalimage") or {}).get("source"))


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sentence_end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if sentence_end >= limit // 2:
        return cut[: sentence_end + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"


_UPLOAD_FILE = re.compile(r"^https://upload\.wikimedia\.org/wikipedia/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/?#]+)")


def _commons_file_name(url: str | None) -> str | None:
    """File name behind an upload.wikimedia.org/…/commons/… URL (``None`` for local uploads)."""
    if not url:
        return None
    m = _UPLOAD_FILE.match(url)
    if not m:
        return None
    return unquote(m.group(1)).replace("_", " ")


# ── Commons ────────────────────────────────────────────────────

_IMAGEINFO = {
    "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 640,
    "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|ObjectName|ImageDescription",
    "format": "json", "formatversion": 2,
}


async def _find_commons_photos(req: EnrichRequest) -> list[EnrichedPhoto]:
    data = await _get_json(COMMONS_API, {
        "action": "query", "generator": "geosearch", "ggscoord": f"{req.lat}|{req.lng}",
        "ggsradius": settings.enrichment_commons_radius_m, "ggslimit": 30, "ggsnamespace": 6,
        **_IMAGEINFO,
    })
    return select_photos(data, req.name)


async def _lead_image(file_name: str) -> EnrichedPhoto | None:
    data = await _get_json(COMMONS_API, {
        "action": "query", "titles": f"File:{file_name}", **_IMAGEINFO,
    })
    photos = select_photos(data, None)
    return photos[0] if photos else None


def select_photos(data: Any, place_name: str | None) -> list[EnrichedPhoto]:
    """Real photos from a Commons ``imageinfo`` response, best first (name match, then nearest)."""
    pages = ((data or {}).get("query") or {}).get("pages") or []
    if isinstance(pages, dict):  # formatversion=1 shape
        pages = list(pages.values())
    wanted = names.distinctive_tokens(place_name)

    ranked: list[tuple[int, int, EnrichedPhoto]] = []
    for page in pages:
        photo = _photo_from_page(page)
        if photo is None:
            continue
        haystack = set(names.tokens(f"{photo.title} {page.get('title', '')}"))
        name_hits = len(wanted & haystack) if wanted else 0
        raw_index = page.get("index")  # the API reports -1 for entries it could not order
        index = raw_index if isinstance(raw_index, int) and raw_index >= 0 else 10_000
        ranked.append((-name_hits, index, photo))
    ranked.sort(key=lambda r: (r[0], r[1]))

    out: list[EnrichedPhoto] = []
    seen: set[str] = set()
    for _, _, photo in ranked:
        if photo.url in seen:
            continue
        seen.add(photo.url)
        out.append(photo)
        if len(out) >= settings.enrichment_max_photos:
            break
    return out


def _photo_from_page(page: Any) -> EnrichedPhoto | None:
    if not isinstance(page, dict):
        return None
    infos = page.get("imageinfo") or []
    if not infos or not isinstance(infos[0], dict):
        return None
    info = infos[0]
    title = str(page.get("title", ""))
    if info.get("mime") not in _PHOTO_MIMES or not title.lower().endswith(_PHOTO_EXTENSIONS):
        return None  # svg logos, gifs, tiffs, audio, video ...
    if _JUNK_TITLE.search(title):
        return None  # logos, flags, maps, diagrams
    width = info.get("width")
    if isinstance(width, int) and width < _MIN_PHOTO_WIDTH:
        return None
    url = _clean_url(info.get("url"))
    if not url:
        return None
    meta = info.get("extmetadata") or {}

    def value(field: str) -> str:
        return _plain_text((meta.get(field) or {}).get("value"))

    return EnrichedPhoto(
        url=url,
        thumb_url=_clean_url(info.get("thumburl")) or url,
        title=value("ObjectName") or re.sub(r"\.\w+$", "", title.removeprefix("File:")),
        author=value("Artist")[:150],
        license=value("LicenseShortName"),
        source_url=_clean_url(info.get("descriptionurl")) or "",
    )


def _plain_text(raw: Any) -> str:
    """HTML fragment → one clean line (Commons ``Artist`` is HTML, often with a licence banner)."""
    if not isinstance(raw, str):
        return ""
    first_block = re.split(r"<(?:div|p|table|br)\b", raw, maxsplit=1, flags=re.IGNORECASE)[0]
    return _SPACES.sub(" ", html.unescape(_TAG.sub("", first_block))).strip()


def _clean_url(url: Any) -> str | None:
    """Drop Wikimedia's ``utm_*`` tracking query; keep only http(s) URLs."""
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    query = "" if parts.query.startswith("utm_") else parts.query
    return urlunsplit(("https", parts.netloc, parts.path, query, ""))
