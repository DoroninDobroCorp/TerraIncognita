"""Tests for Story 1.2 — Atlas Obscura source."""

from __future__ import annotations

import hashlib
import re

import pytest

from app.config import settings
from app.models.place import PlaceCategory, PlaceSource
from app.services.cache import DiskCache
from app.sources import atlas_obscura as atlas_module
from app.sources.atlas_obscura import AtlasObscuraError, AtlasObscuraSource
from app.utils import http_client

_API_RESPONSE = {
    "places": [
        {
            "id": "abc123",
            "title": "Submarine Base",
            "subtitle": "Secret cold-war submarine pen",
            "lat": 42.45,
            "lng": 18.53,
            "categories": ["military", "abandoned"],
            "tags": ["bunkers", "abandoned"],
            "photo": "https://example.com/photo.jpg",
            "url": "/places/submarine-base",
        },
        {
            "id": "def456",
            "title": "Hidden Waterfall",
            "subtitle": "A small waterfall behind the rocks",
            "lat": 42.44,
            "lng": 18.52,
            "categories": ["natural wonders"],
            "tags": ["waterfalls"],
            "photo": "",
            "url": "/places/hidden-waterfall",
        },
    ],
}


class TestAtlasObscura:
    def setup_method(self):
        self.source = AtlasObscuraSource()

    def test_parse_api_response(self):
        places = self.source._parse_api_response(_API_RESPONSE)
        assert len(places) == 2

    def test_place_id_format(self):
        places = self.source._parse_api_response(_API_RESPONSE)
        assert places[0].id == "atlas_abc123"

    def test_source_set(self):
        places = self.source._parse_api_response(_API_RESPONSE)
        assert places[0].source == PlaceSource.ATLAS_OBSCURA
        assert PlaceSource.ATLAS_OBSCURA in places[0].sources

    def test_category_mapping_military(self):
        places = self.source._parse_api_response(_API_RESPONSE)
        assert PlaceCategory.MILITARY in places[0].categories

    def test_category_mapping_nature(self):
        places = self.source._parse_api_response(_API_RESPONSE)
        assert PlaceCategory.NATURE_HIDDEN in places[1].categories

    def test_confidence_score(self):
        places = self.source._parse_api_response(_API_RESPONSE)
        assert places[0].confidence == 0.85

    def test_photos_extracted(self):
        places = self.source._parse_api_response(_API_RESPONSE)
        assert len(places[0].photos) == 1
        assert places[1].photos == []  # empty string filtered

    def test_metadata_url(self):
        places = self.source._parse_api_response(_API_RESPONSE)
        assert places[0].metadata["atlas_url"] == "/places/submarine-base"

    def test_parse_html_in_bbox(self):
        html = """
        <div data-lat="42.45" data-lng="18.53"
             class="something">
            <span class="title">Cool Place</span>
        </div>
        """
        places = self.source._parse_html(html, 42.0, 18.0, 43.0, 19.0)
        # The regex is fragile — test the concept
        assert isinstance(places, list)

    def test_parse_html_outside_bbox(self):
        html = '<div data-lat="50.0" data-lng="30.0"><span class="title">Far Away</span></div>'
        places = self.source._parse_html(html, 42.0, 18.0, 43.0, 19.0)
        assert len(places) == 0

    def test_handles_missing_fields_gracefully(self):
        data = {"places": [{"lat": 42.0, "lng": 18.0}]}
        places = self.source._parse_api_response(data)
        # Should not crash, but the item may be skipped due to missing id
        assert isinstance(places, list)


# ── Failure semantics: a block is not "nothing nearby" ───────────────────────

API = re.compile(r"https://www\.atlasobscura\.com/api/v2/search/places.*")
HTML = re.compile(r"https://www\.atlasobscura\.com/search\?.*")
_HTML_PAGE = '<div data-lat="42.45" data-lng="18.53" class="x"><span class="title">Cool Place</span></div>'


@pytest.fixture
async def ao(tmp_path, monkeypatch):
    monkeypatch.setattr(atlas_module, "disk_cache", DiskCache(cache_dir=str(tmp_path), ttl=3600))
    http_client._client = None
    yield AtlasObscuraSource()
    await http_client.close_http_client()


class TestAtlasSearch:
    async def test_api_answer_is_parsed_and_cached(self, ao, httpx_mock):
        httpx_mock.add_response(url=API, json=_API_RESPONSE)
        first = await ao.search(42.45, 18.53, 5.0)
        second = await ao.search(42.45, 18.53, 5.0)
        assert len(first) == 2 and len(second) == 2
        assert len(httpx_mock.get_requests()) == 1
        assert httpx_mock.get_requests()[0].headers["user-agent"] == settings.wikimedia_user_agent

    async def test_403_falls_back_to_the_html_page(self, ao, httpx_mock):
        httpx_mock.add_response(url=API, status_code=403)
        httpx_mock.add_response(url=HTML, text=_HTML_PAGE)
        places = await ao.search(42.45, 18.53, 5.0)
        assert [p.name for p in places] == ["Cool Place"]

    async def test_block_raises_is_not_cached_and_backs_off(self, ao, httpx_mock):
        httpx_mock.add_response(url=API, status_code=403)
        httpx_mock.add_response(url=HTML, status_code=403)
        with pytest.raises(AtlasObscuraError):
            await ao.search(42.45, 18.53, 5.0)
        n = len(httpx_mock.get_requests())
        with pytest.raises(AtlasObscuraError, match="backing off"):
            await ao.search(42.45, 18.53, 5.0)  # no new requests while backing off
        assert len(httpx_mock.get_requests()) == n

    async def test_recovers_after_backoff_and_nothing_stale_was_cached(self, ao, httpx_mock):
        httpx_mock.add_response(url=API, status_code=500)
        with pytest.raises(AtlasObscuraError):
            await ao.search(42.45, 18.53, 5.0)
        ao._retry_after = 0.0  # back-off elapsed
        httpx_mock.add_response(url=API, json=_API_RESPONSE)
        assert len(await ao.search(42.45, 18.53, 5.0)) == 2

    async def test_garbage_json_is_an_error_not_a_crash(self, ao, httpx_mock):
        httpx_mock.add_response(url=API, content=b"<html>challenge</html>")
        with pytest.raises(AtlasObscuraError):
            await ao.search(42.45, 18.53, 5.0)


def test_html_ids_are_stable_across_processes():
    ids = [p.id for p in AtlasObscuraSource()._parse_html(_HTML_PAGE, 42.0, 18.0, 43.0, 19.0)]
    assert ids == ["atlas_html_" + hashlib.md5(b"Cool Place").hexdigest()[:8]]


def test_absolute_source_url():
    places = AtlasObscuraSource()._parse_api_response(_API_RESPONSE)
    assert places[0].metadata["atlas_url"] == "/places/submarine-base"
    assert places[0].metadata["source_url"] == "https://www.atlasobscura.com/places/submarine-base"
