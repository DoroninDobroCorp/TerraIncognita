"""Tests for POST /api/place/enrich: Wikipedia summary + Commons photos (no network)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.place import EnrichedPhoto, EnrichRequest, EnrichResponse
from app.services import enrichment
from app.services.cache import DiskCache
from app.services.enrichment import (
    cache_key,
    enrich_place,
    pick_geosearch_title,
    select_photos,
)
from app.utils import http_client
from app.utils.rate_limiter import RateLimiter

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


WIKI_SUMMARY = fixture("wikipedia_summary_mamula_en.json")
WIKIDATA_SITELINKS = fixture("wikidata_sitelinks_mamula.json")
WIKI_GEOSEARCH = fixture("wikipedia_geosearch_mamula.json")
COMMONS_GEOSEARCH = fixture("commons_geosearch_mamula.json")

# A Commons "titles=" answer for the article's lead image (shape of the real API).
LEAD_PAGE = {"query": {"pages": [{
    "pageid": 1, "ns": 6, "title": "File:Mamula Island by Klackalica.jpg",
    "imageinfo": [{
        "mime": "image/jpeg", "width": 800, "height": 602,
        "url": "https://upload.wikimedia.org/wikipedia/commons/2/23/Mamula_Island_by_Klackalica.jpg",
        "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/Mamula_Island_by_Klackalica.jpg/640px-Mamula_Island_by_Klackalica.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Mamula_Island_by_Klackalica.jpg",
        "extmetadata": {
            "ObjectName": {"value": "Mamula Island by Klackalica"},
            "Artist": {"value": '<a href="//commons.wikimedia.org/wiki/User:Klackalica">Klackalica</a>'},
            "LicenseShortName": {"value": "CC BY-SA 3.0"},
        },
    }],
}]}}

REQ = EnrichRequest(name="Fort Mamula", lat=42.3953, lng=18.5583, wikidata_id="Q260715", lang="en")


class FakeWikimedia:
    """Scriptable stand-in for Wikidata + Wikipedia + Commons; records every request."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.routes: dict[str, object] = {}

    def route(self, kind: str, response: object) -> None:
        """kind: ``wikidata`` | ``geosearch:<lang>`` | ``summary:<lang>`` | ``commons-geo`` | ``commons-titles``."""
        self.routes[kind] = response

    def kinds(self) -> list[str]:
        return [self._kind(r) for r in self.calls]

    @staticmethod
    def _kind(request: httpx.Request) -> str:
        host, path, params = request.url.host, request.url.path, request.url.params
        lang = host.split(".")[0]
        if host == "www.wikidata.org":
            return "wikidata"
        if host == "commons.wikimedia.org":
            return "commons-titles" if "titles" in params else "commons-geo"
        if path.startswith("/api/rest_v1/page/summary/"):
            return f"summary:{lang}"
        if params.get("list") == "geosearch":
            return f"geosearch:{lang}"
        return f"unknown:{request.url}"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        response = self.routes.get(self._kind(request))
        if response is None:
            return httpx.Response(404, json={})
        if callable(response):
            return response(request)
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, json=response)


@pytest.fixture
async def wm(tmp_path, monkeypatch, httpx_mock):
    monkeypatch.setattr(enrichment, "enrichment_cache", DiskCache(cache_dir=str(tmp_path), ttl=0))
    monkeypatch.setattr(enrichment, "_limiter", RateLimiter(max_requests=1000, window_seconds=1))
    monkeypatch.setattr(enrichment, "_inflight", {})
    http_client._client = None
    fake = FakeWikimedia()
    httpx_mock.add_callback(fake, is_reusable=True, is_optional=True)
    yield fake
    await http_client.close_http_client()


@pytest.fixture
def full(wm):
    """Everything Wikimedia knows about Mamula, as recorded from the live APIs."""
    wm.route("wikidata", WIKIDATA_SITELINKS)
    wm.route("summary:en", WIKI_SUMMARY)
    wm.route("commons-geo", COMMONS_GEOSEARCH)
    wm.route("commons-titles", LEAD_PAGE)
    return wm


# ── Happy path ───────────────────────────────────────────────────────────────


class TestHappyPath:
    async def test_wikipedia_via_wikidata_sitelink(self, full):
        result = await enrich_place(REQ)
        assert result.wikipedia is not None
        assert result.wikipedia.title == "Mamula"
        assert result.wikipedia.lang == "en"
        assert result.wikipedia.url == "https://en.wikipedia.org/wiki/Mamula"
        assert "Mamula" in result.wikipedia.extract and len(result.wikipedia.extract) > 50
        assert not any(k.startswith("geosearch") for k in full.kinds())  # sitelink was enough

    async def test_photos_have_attribution_and_clean_urls(self, full):
        result = await enrich_place(REQ)
        assert 1 <= len(result.photos) <= settings.enrichment_max_photos
        for photo in result.photos:
            assert photo.url.startswith("https://upload.wikimedia.org/")
            assert photo.thumb_url.startswith("https://")
            assert photo.source_url.startswith("https://commons.wikimedia.org/wiki/File:")
            assert photo.license and photo.author and photo.title
            assert "utm_" not in photo.url + photo.thumb_url
            assert "<" not in photo.author and "\n" not in photo.author

    async def test_commons_html_author_is_reduced_to_a_name(self, full):
        result = await enrich_place(REQ)
        by_title = {p.title: p for p in result.photos}
        assert by_title["Kotor Bay of Kotor 25"].author == "Ad Meskens"
        assert by_title["Kotor Bay of Kotor 25"].license == "CC BY-SA 4.0"
        assert by_title["Luštica - panoramio"].author == "BrsJvnvc"
        assert by_title["Luštica - panoramio"].license == "CC BY 3.0"

    async def test_lead_image_of_the_article_comes_first(self, full):
        result = await enrich_place(REQ)
        assert result.photos[0].title == "Mamula Island by Klackalica"
        assert result.photos[0].author == "Klackalica"
        assert result.photos[0].license == "CC BY-SA 3.0"
        assert len(result.photos) == 1 + len(select_photos(COMMONS_GEOSEARCH, None))
        titles = [req.url.params.get("titles") for req in full.calls if "titles" in req.url.params]
        assert titles == ["File:Mamula Island by Klackalica.jpg"]

    async def test_sources_list_reports_who_contributed(self, full):
        assert (await enrich_place(REQ)).sources == ["wikipedia", "commons"]

    async def test_response_contract_is_exact(self, full):
        data = (await enrich_place(REQ)).model_dump(mode="json")
        assert set(data) == {"wikipedia", "photos", "sources"}
        assert set(data["wikipedia"]) == {"title", "extract", "url", "lang"}
        assert set(data["photos"][0]) == {"url", "thumb_url", "title", "author", "license", "source_url"}

    async def test_every_request_is_polite(self, full):
        await enrich_place(REQ)
        assert full.calls
        for request in full.calls:
            assert request.headers["user-agent"] == settings.wikimedia_user_agent
            assert "TerraIncognita" in request.headers["user-agent"]
            assert request.method == "GET"

    async def test_every_request_goes_through_the_rate_limiter(self, full, monkeypatch):
        limiter = RateLimiter(1000, 1)
        limiter.acquire = AsyncMock(wraps=limiter.acquire)
        monkeypatch.setattr(enrichment, "_limiter", limiter)
        await enrich_place(REQ)
        assert limiter.acquire.await_count == len(full.calls)

    async def test_commons_search_parameters(self, full):
        await enrich_place(REQ)
        params = next(r for r in full.calls if full._kind(r) == "commons-geo").url.params
        assert params["generator"] == "geosearch" and params["ggsnamespace"] == "6"
        assert params["ggscoord"] == "42.3953|18.5583"
        assert params["prop"] == "imageinfo" and params["iiurlwidth"] == "640"
        assert "extmetadata" in params["iiprop"] and "url" in params["iiprop"]


# ── Language handling ────────────────────────────────────────────────────────


RU_SUMMARY = {"type": "standard", "title": "Мамула (остров)", "lang": "ru",
              "extract": "Мамула — остров в Черногории, на котором расположена крепость.",
              "content_urls": {"desktop": {"page": "https://ru.wikipedia.org/wiki/Мамула_(остров)"}}}


class TestLanguages:
    async def test_requested_language_is_tried_first(self, full):
        full.route("wikidata", {"entities": {"Q260715": {"sitelinks": {
            "ruwiki": {"title": "Мамула (остров)"}, "enwiki": {"title": "Mamula"}}}}})
        full.route("summary:ru", RU_SUMMARY)
        result = await enrich_place(REQ.model_copy(update={"lang": "ru"}))
        assert result.wikipedia.lang == "ru" and result.wikipedia.title == "Мамула (остров)"
        assert "summary:en" not in full.kinds()
        summary_call = next(r for r in full.calls if full._kind(r) == "summary:ru")
        assert "%D0%9C" in str(summary_call.url)  # title is percent-encoded

    async def test_falls_back_to_english_when_requested_language_has_no_article(self, full):
        # wikidata has only an enwiki sitelink; the ru geosearch finds nothing
        full.route("geosearch:ru", {"query": {"geosearch": []}})
        result = await enrich_place(REQ.model_copy(update={"lang": "ru"}))
        assert result.wikipedia.lang == "en" and result.wikipedia.title == "Mamula"

    async def test_falls_back_to_english_when_requested_language_summary_is_missing(self, full):
        full.route("wikidata", {"entities": {"Q260715": {"sitelinks": {
            "ruwiki": {"title": "Мамула"}, "enwiki": {"title": "Mamula"}}}}})
        full.route("summary:ru", httpx.Response(404, json={"type": "https://mediawiki.org/wiki/HyperSwitch/errors/not_found"}))
        result = await enrich_place(REQ.model_copy(update={"lang": "ru"}))
        assert result.wikipedia.lang == "en"

    async def test_english_request_never_queries_another_language(self, full):
        await enrich_place(REQ)
        assert not any(k.endswith(":ru") for k in full.kinds())
        wikidata = next(r for r in full.calls if full._kind(r) == "wikidata")
        assert wikidata.url.params["sitefilter"] == "enwiki"

    async def test_wikidata_request_asks_for_both_languages(self, full):
        await enrich_place(REQ.model_copy(update={"lang": "ru"}))
        wikidata = next(r for r in full.calls if full._kind(r) == "wikidata")
        assert wikidata.url.params["sitefilter"] == "ruwiki|enwiki"


# ── Finding the article without a Wikidata id ────────────────────────────────


class TestGeosearch:
    async def test_geosearch_finds_article_by_name(self, wm):
        wm.route("geosearch:en", WIKI_GEOSEARCH)
        wm.route("summary:en", WIKI_SUMMARY)
        wm.route("commons-geo", {"batchcomplete": True})
        result = await enrich_place(REQ.model_copy(update={"wikidata_id": None}))
        assert result.wikipedia.title == "Mamula"
        assert "wikidata" not in wm.kinds()
        params = next(r for r in wm.calls if wm._kind(r) == "geosearch:en").url.params
        assert params["gscoord"] == "42.3953|18.5583" and params["list"] == "geosearch"

    async def test_wrong_article_nearby_is_not_attached(self, wm):
        wm.route("geosearch:en", {"query": {"geosearch": [
            {"title": "Church of Saint Nicholas", "dist": 90.0}]}})
        wm.route("summary:en", WIKI_SUMMARY)
        result = await enrich_place(REQ.model_copy(update={"wikidata_id": None}))
        assert result.wikipedia is None
        assert "summary:en" not in wm.kinds()

    async def test_disambiguation_pages_are_skipped(self, wm):
        wm.route("geosearch:en", WIKI_GEOSEARCH)
        wm.route("summary:en", {"type": "disambiguation", "title": "Mamula", "extract": "Mamula may refer to:"})
        result = await enrich_place(REQ.model_copy(update={"wikidata_id": None}))
        assert result.wikipedia is None

    async def test_pages_without_extract_are_skipped(self, wm):
        wm.route("geosearch:en", WIKI_GEOSEARCH)
        wm.route("summary:en", {"type": "standard", "title": "Mamula", "extract": "  "})
        assert (await enrich_place(REQ.model_copy(update={"wikidata_id": None}))).wikipedia is None

    async def test_sitelink_falls_through_to_geosearch_when_summary_missing(self, wm):
        wm.route("wikidata", WIKIDATA_SITELINKS)
        wm.route("summary:en", httpx.Response(404, json={}))
        wm.route("geosearch:en", {"query": {"geosearch": []}})
        assert (await enrich_place(REQ)).wikipedia is None


HITS = [
    {"title": "Church of Saint Nicholas", "dist": 20.0},
    {"title": "Mamula (island)", "dist": 78.0},
    {"title": "Hotel Mamula Palace", "dist": 40.0},
]


@pytest.mark.parametrize(("name", "hits", "expected"), [
    ("Fort Mamula", HITS, "Mamula (island)"),            # the name match beats the nearer church
    ("Tvrđava Mamula", HITS, "Mamula (island)"),
    ("Форт Мамула", HITS, "Mamula (island)"),            # Cyrillic query, Latin titles
    ("Mamula", [{"title": "Mamula", "dist": 900.0}, {"title": "Other", "dist": 5.0}], "Mamula"),
    ("Fort Arza", HITS, None),                           # real name, nothing matches
    ("Fort Arza", [{"title": "Unrelated", "dist": 12.0}], "Unrelated"),  # …but the very same spot is trusted
    ("Fort Arza", [{"title": "Unrelated", "dist": 25.0}], None),
    (None, HITS, "Church of Saint Nicholas"),            # no name: the nearest hit within 150 m
    ("Bunker", HITS, "Church of Saint Nicholas"),        # generic name: same
    ("Abandoned building", [{"title": "Far", "dist": 400.0}], None),
    (None, [], None),
    ("Fort Mamula", [{"nonsense": 1}, "garbage", None], None),
    ("Fort Mamula", [{"title": "Mamula"}], "Mamula"),    # no dist field
])
def test_pick_geosearch_title(name, hits, expected):
    assert pick_geosearch_title(hits, name) == expected


# ── Failure isolation ────────────────────────────────────────────────────────


class TestGracefulDegradation:
    async def test_wikipedia_down_still_returns_photos(self, full):
        full.route("summary:en", httpx.Response(503))
        full.route("geosearch:en", httpx.Response(503))
        result = await enrich_place(REQ)
        assert result.wikipedia is None
        assert result.photos and result.sources == ["commons"]

    async def test_commons_down_still_returns_wikipedia(self, full):
        full.route("commons-geo", httpx.Response(500))
        result = await enrich_place(REQ)
        assert result.wikipedia.title == "Mamula"
        assert result.photos[0].title == "Mamula Island by Klackalica"  # the lead image still works
        assert result.sources == ["wikipedia", "commons"]

    async def test_everything_down_returns_empty_response(self, wm):
        for kind in ("wikidata", "geosearch:en", "summary:en", "commons-geo"):
            wm.route(kind, httpx.Response(500))
        result = await enrich_place(REQ)
        assert result == EnrichResponse()

    async def test_garbage_json_is_survivable(self, wm):
        for kind in ("wikidata", "geosearch:en", "summary:en", "commons-geo"):
            wm.route(kind, httpx.Response(200, content=b"<html>not json</html>"))
        assert (await enrich_place(REQ)).wikipedia is None

    @pytest.mark.parametrize("junk", [[], "x", 5, None, {"query": []}, {"query": {"pages": "x"}}])
    async def test_unexpected_json_shapes_are_survivable(self, wm, junk):
        for kind in ("wikidata", "geosearch:en", "summary:en", "commons-geo"):
            wm.route(kind, httpx.Response(200, json=junk))
        result = await enrich_place(REQ)
        assert result.wikipedia is None and result.photos == []

    async def test_wikidata_outage_falls_back_to_geosearch_but_is_not_cached(self, wm):
        wm.route("wikidata", httpx.Response(502))
        wm.route("geosearch:en", WIKI_GEOSEARCH)
        wm.route("summary:en", WIKI_SUMMARY)
        wm.route("commons-geo", {"batchcomplete": True})
        first = await enrich_place(REQ)
        assert first.wikipedia.title == "Mamula"
        n = len(wm.calls)
        await enrich_place(REQ)
        assert len(wm.calls) > n  # not remembered: Wikidata may be back next time

    async def test_one_language_failing_does_not_lose_the_other(self, wm):
        wm.route("geosearch:ru", httpx.Response(500))
        wm.route("geosearch:en", WIKI_GEOSEARCH)
        wm.route("summary:en", WIKI_SUMMARY)
        wm.route("commons-geo", {"batchcomplete": True})
        req = REQ.model_copy(update={"wikidata_id": None, "lang": "ru"})
        assert (await enrich_place(req)).wikipedia.lang == "en"


# ── Caching ──────────────────────────────────────────────────────────────────


class TestCaching:
    async def test_cache_never_expires(self):
        assert enrichment.enrichment_cache._ttl == 0

    async def test_second_identical_request_makes_no_http_calls(self, full):
        first = await enrich_place(REQ)
        n = len(full.calls)
        second = await enrich_place(REQ)
        assert len(full.calls) == n and second == first

    async def test_cached_value_survives_a_new_cache_object(self, full, tmp_path):
        first = await enrich_place(REQ)
        reopened = DiskCache(cache_dir=str(tmp_path), ttl=0)
        raw = await reopened.get(cache_key(REQ))
        assert EnrichResponse.model_validate(raw) == first

    async def test_nearby_clicks_share_the_cache_entry(self, full):
        await enrich_place(REQ)
        n = len(full.calls)
        await enrich_place(REQ.model_copy(update={"lat": REQ.lat + 0.00002, "lng": REQ.lng - 0.00002}))
        assert len(full.calls) == n

    def test_cache_key_distinguishes_what_matters(self):
        base = cache_key(REQ)
        assert cache_key(REQ.model_copy(update={"lang": "ru"})) != base
        assert cache_key(REQ.model_copy(update={"lat": REQ.lat + 0.01})) != base
        assert cache_key(REQ.model_copy(update={"name": "Other"})) != base
        assert cache_key(REQ.model_copy(update={"wikidata_id": None})) != base
        assert cache_key(REQ.model_copy(update={"name": "fort  MAMULA!"})) == base  # name is normalised

    async def test_empty_but_successful_answers_are_cached_too(self, wm):
        wm.route("geosearch:en", {"query": {"geosearch": []}})
        wm.route("commons-geo", {"batchcomplete": True})
        req = REQ.model_copy(update={"wikidata_id": None})
        await enrich_place(req)
        n = len(wm.calls)
        assert (await enrich_place(req)) == EnrichResponse()
        assert len(wm.calls) == n

    async def test_outage_responses_are_not_cached(self, full):
        full.route("commons-geo", httpx.Response(500))
        await enrich_place(REQ)
        full.route("commons-geo", COMMONS_GEOSEARCH)
        again = await enrich_place(REQ)
        assert len(again.photos) > 1  # the recovered Commons answer is used

    async def test_unreadable_cache_entry_is_recomputed(self, full):
        await enrichment.enrichment_cache.set(cache_key(REQ), {"photos": "garbage"})
        assert (await enrich_place(REQ)).wikipedia.title == "Mamula"

    async def test_concurrent_identical_requests_share_one_set_of_calls(self, full):
        a, b, c = await asyncio.gather(enrich_place(REQ), enrich_place(REQ), enrich_place(REQ))
        assert a == b == c
        assert full.kinds().count("wikidata") == 1
        assert full.kinds().count("commons-geo") == 1


# ── Photo selection ──────────────────────────────────────────────────────────


def page(title, *, mime="image/jpeg", width=2000, index=0, url=None, **meta) -> dict:
    url = url or f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{title.removeprefix('File:').replace(' ', '_')}"
    ext = {k: {"value": v} for k, v in meta.items()}
    return {"title": title, "index": index, "imageinfo": [{
        "mime": mime, "width": width, "height": 1000, "url": url + "?utm_source=commons.wikimedia.org&utm_campaign=index",
        "thumburl": url.replace("/commons/", "/commons/thumb/") + "/640px-x.jpg?utm_source=commons.wikimedia.org",
        "descriptionurl": "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_"),
        "extmetadata": ext,
    }]}


def answer(*pages) -> dict:
    return {"query": {"pages": list(pages)}}


class TestSelectPhotos:
    def test_non_photos_and_junk_are_dropped(self):
        data = answer(
            page("File:Fort.jpg", index=0),
            page("File:Fort logo.svg", mime="image/svg+xml", index=1),
            page("File:Fort animation.gif", mime="image/gif", index=2),
            page("File:Fort scan.tif", mime="image/tiff", index=3),
            page("File:Fort tour.webm", mime="video/webm", index=4),
            page("File:Fort coat of arms.jpg", index=5),
            page("File:Map of Fort area.png", mime="image/png", index=6),
            page("File:Flag of Montenegro.png", mime="image/png", index=7),
            page("File:Fort diagram.jpg", index=8),
            page("File:Tiny fort.jpg", width=200, index=9),
            page("File:Fort night.JPG", index=10),
        )
        assert [p.title for p in select_photos(data, None)] == ["Fort", "Fort night"]

    def test_order_is_name_match_first_then_distance(self):
        data = answer(
            page("File:Beach.jpg", index=0), page("File:Mamula fortress.jpg", index=3),
            page("File:Tower.jpg", index=1), page("File:Mamula gate.jpg", index=2),
        )
        assert [p.title for p in select_photos(data, "Fort Mamula")] == [
            "Mamula gate", "Mamula fortress", "Beach", "Tower"]
        assert [p.title for p in select_photos(data, None)] == ["Beach", "Tower", "Mamula gate", "Mamula fortress"]

    def test_index_minus_one_sorts_last(self):
        data = answer(page("File:Odd.jpg", index=-1), page("File:Near.jpg", index=0))
        assert [p.title for p in select_photos(data, None)] == ["Near", "Odd"]

    def test_at_most_max_photos(self):
        data = answer(*[page(f"File:P{i}.jpg", index=i) for i in range(20)])
        assert len(select_photos(data, None)) == settings.enrichment_max_photos == 6

    def test_duplicates_are_removed(self):
        data = answer(page("File:A.jpg", index=0), page("File:A.jpg", index=1))
        assert len(select_photos(data, None)) == 1

    def test_attribution_is_cleaned(self):
        data = answer(page(
            "File:A.jpg", ObjectName="A &amp; B", LicenseShortName="CC BY-SA 4.0",
            Artist='<a href="//x">Jane &quot;JD&quot; Doe</a><div style="clear:both"><p>You are free…</p></div>',
        ))
        [photo] = select_photos(data, None)
        assert photo.title == "A & B"
        assert photo.author == 'Jane "JD" Doe'
        assert photo.license == "CC BY-SA 4.0"
        assert photo.url == "https://upload.wikimedia.org/wikipedia/commons/a/ab/A.jpg"
        assert photo.thumb_url.endswith("640px-x.jpg")
        assert photo.source_url == "https://commons.wikimedia.org/wiki/File:A.jpg"

    def test_missing_metadata_does_not_drop_the_photo(self):
        [photo] = select_photos(answer(page("File:Bare name.jpg")), None)
        assert photo.title == "Bare name" and photo.author == "" and photo.license == ""

    def test_long_author_is_truncated(self):
        [photo] = select_photos(answer(page("File:A.jpg", Artist="x" * 500)), None)
        assert len(photo.author) == 150

    def test_malformed_pages_are_ignored(self):
        data = answer({"title": "File:X.jpg"}, {"title": "File:Y.jpg", "imageinfo": []}, "junk",
                      {"title": "File:Z.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 999}]},
                      page("File:Good.jpg"))
        assert [p.title for p in select_photos(data, None)] == ["Good"]

    def test_format_version_one_shape(self):
        data = {"query": {"pages": {"123": page("File:Old.jpg")}}}
        assert [p.title for p in select_photos(data, None)] == ["Old"]

    def test_non_http_urls_are_rejected(self):
        bad = page("File:Bad.jpg")
        bad["imageinfo"][0]["url"] = "javascript:alert(1)"
        assert select_photos(answer(bad), None) == []


# ── The HTTP endpoint ────────────────────────────────────────────────────────

client = TestClient(app)
BODY = {"name": "Fort Mamula", "lat": 42.3953, "lng": 18.5583, "wikidata_id": "Q260715", "lang": "en"}


class TestEndpoint:
    def test_roundtrip(self):
        canned = EnrichResponse(
            wikipedia={"title": "Mamula", "extract": "An island.", "url": "https://en.wikipedia.org/wiki/Mamula", "lang": "en"},
            photos=[EnrichedPhoto(url="https://u/a.jpg", thumb_url="https://u/t.jpg", title="A",
                                  author="Me", license="CC0", source_url="https://c/File:A.jpg")],
            sources=["wikipedia", "commons"],
        )
        with patch("app.api.enrich.enrich_place", new=AsyncMock(return_value=canned)) as mock:
            resp = client.post("/api/place/enrich", json=BODY)
        assert resp.status_code == 200
        assert resp.json() == {
            "wikipedia": {"title": "Mamula", "extract": "An island.",
                          "url": "https://en.wikipedia.org/wiki/Mamula", "lang": "en"},
            "photos": [{"url": "https://u/a.jpg", "thumb_url": "https://u/t.jpg", "title": "A",
                        "author": "Me", "license": "CC0", "source_url": "https://c/File:A.jpg"}],
            "sources": ["wikipedia", "commons"],
        }
        sent = mock.call_args[0][0]
        assert (sent.name, sent.lat, sent.lng, sent.wikidata_id, sent.lang) == (
            "Fort Mamula", 42.3953, 18.5583, "Q260715", "en")

    def test_minimal_body_uses_defaults(self):
        with patch("app.api.enrich.enrich_place", new=AsyncMock(return_value=EnrichResponse())) as mock:
            resp = client.post("/api/place/enrich", json={"lat": 1.0, "lng": 2.0})
        assert resp.status_code == 200
        assert resp.json() == {"wikipedia": None, "photos": [], "sources": []}
        sent = mock.call_args[0][0]
        assert sent.name is None and sent.wikidata_id is None and sent.lang == "en"

    def test_explicit_nulls_are_accepted(self):
        with patch("app.api.enrich.enrich_place", new=AsyncMock(return_value=EnrichResponse())):
            resp = client.post("/api/place/enrich", json={"name": None, "lat": 1, "lng": 2, "wikidata_id": None})
        assert resp.status_code == 200

    def test_blank_name_becomes_none(self):
        with patch("app.api.enrich.enrich_place", new=AsyncMock(return_value=EnrichResponse())) as mock:
            client.post("/api/place/enrich", json={"name": "   ", "lat": 1, "lng": 2})
        assert mock.call_args[0][0].name is None

    @pytest.mark.parametrize("body", [
        {"lat": 91, "lng": 0}, {"lat": -91, "lng": 0}, {"lat": 0, "lng": 181}, {"lat": 0, "lng": -181},
        {"lat": "north", "lng": 0}, {"lng": 0}, {"lat": 0}, {},
        {"lat": 0, "lng": 0, "name": "x" * 201},
        {"lat": 0, "lng": 0, "wikidata_id": "P31"}, {"lat": 0, "lng": 0, "wikidata_id": "Q"},
        {"lat": 0, "lng": 0, "wikidata_id": "Q1; DROP"}, {"lat": 0, "lng": 0, "wikidata_id": "Q0"},
        {"lat": 0, "lng": 0, "lang": "english"}, {"lat": 0, "lng": 0, "lang": "EN"},
        {"lat": 0, "lng": 0, "lang": "e"}, {"lat": 0, "lng": 0, "lang": "evil.com/x"},
        {"lat": 0, "lng": 0, "lang": ""},
    ])
    def test_invalid_input_is_rejected_before_any_outgoing_call(self, body):
        with patch("app.api.enrich.enrich_place", new=AsyncMock()) as mock:
            resp = client.post("/api/place/enrich", json=body)
        assert resp.status_code == 422
        mock.assert_not_called()

    def test_boundary_values_are_valid(self):
        with patch("app.api.enrich.enrich_place", new=AsyncMock(return_value=EnrichResponse())):
            for body in ({"lat": 90, "lng": 180}, {"lat": -90, "lng": -180, "name": "x" * 200, "lang": "ceb"}):
                assert client.post("/api/place/enrich", json=body).status_code == 200

    def test_unexpected_service_error_never_becomes_a_500(self):
        with patch("app.api.enrich.enrich_place", new=AsyncMock(side_effect=RuntimeError("boom"))):
            resp = client.post("/api/place/enrich", json=BODY)
        assert resp.status_code == 200
        assert resp.json() == {"wikipedia": None, "photos": [], "sources": []}

    def test_wikipedia_outage_end_to_end_is_a_200(self, tmp_path, monkeypatch, httpx_mock):
        monkeypatch.setattr(enrichment, "enrichment_cache", DiskCache(cache_dir=str(tmp_path), ttl=0))
        monkeypatch.setattr(enrichment, "_limiter", RateLimiter(1000, 1))
        monkeypatch.setattr(http_client, "_client", None)
        httpx_mock.add_callback(lambda request: httpx.Response(503), is_reusable=True, is_optional=True)
        resp = client.post("/api/place/enrich", json=BODY)
        assert resp.status_code == 200
        assert resp.json() == {"wikipedia": None, "photos": [], "sources": []}

    def test_get_is_not_allowed(self):
        assert client.get("/api/place/enrich").status_code == 405

    def test_endpoint_is_rate_limited_at_30_per_minute(self):
        with patch("app.api.enrich.enrich_place", new=AsyncMock(return_value=EnrichResponse())):
            codes = [client.post("/api/place/enrich", json=BODY).status_code for _ in range(31)]
        assert codes[:30] == [200] * 30
        assert codes[30] == 429

    def test_rate_rule_is_separate_from_discovery(self):
        from app.middleware import RATE_RULES

        rule = next(r for r in RATE_RULES if r.name == "enrichment")
        assert rule.limit == 30 and rule.matches("POST", "/api/place/enrich")
        assert not rule.matches("GET", "/api/place/enrich")
        discovery = next(r for r in RATE_RULES if r.name == "discovery")
        assert not discovery.matches("POST", "/api/place/enrich")
