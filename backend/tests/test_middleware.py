"""Tests for rate limiting, scanner blocking, security headers and misc endpoints."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.middleware import RATE_RULES, RateRule, SlidingWindowLimiter, client_ip

client = TestClient(app)


class TestSlidingWindowLimiter:
    def test_allows_up_to_limit_then_blocks(self):
        lim = SlidingWindowLimiter()
        rule = RateRule("t", limit=2, window=10)
        assert lim.check(rule, "a", now=0) == 0
        assert lim.check(rule, "a", now=1) == 0
        wait = lim.check(rule, "a", now=2)
        assert wait == pytest.approx(8)

    def test_window_slides(self):
        lim = SlidingWindowLimiter()
        rule = RateRule("t", limit=1, window=10)
        assert lim.check(rule, "a", now=0) == 0
        assert lim.check(rule, "a", now=5) > 0
        assert lim.check(rule, "a", now=10.5) == 0

    def test_clients_and_rules_are_independent(self):
        lim = SlidingWindowLimiter()
        r1, r2 = RateRule("one", limit=1), RateRule("two", limit=1)
        assert lim.check(r1, "a", now=0) == 0
        assert lim.check(r1, "b", now=0) == 0
        assert lim.check(r2, "a", now=0) == 0

    def test_stale_keys_are_swept(self):
        lim = SlidingWindowLimiter()
        lim._last_sweep = 0.0
        rule = RateRule("t", limit=5, window=10)
        for i in range(100):
            lim.check(rule, f"ip{i}", now=0)
        lim.check(rule, "fresh", now=lim._SWEEP_EVERY + 1000)
        assert len(lim._hits) == 1


class TestRules:
    @staticmethod
    def _rule(method: str, path: str):
        return next((r for r in RATE_RULES if r.matches(method, path)), None)

    def test_llm_endpoint_only_limited_on_post(self):
        assert self._rule("POST", "/api/chat").name == "LLM"
        assert self._rule("GET", "/api/chat") is None

    def test_journal_sub_resources_are_covered(self):
        assert self._rule("GET", "/api/visits/abc").name == "journal"
        assert self._rule("DELETE", "/api/trips/1").name == "journal"

    def test_unrelated_paths_unlimited(self):
        assert self._rule("GET", "/health") is None
        assert self._rule("GET", "/map/app.html") is None


class TestClientIp:
    @staticmethod
    def _req(xff: str | None, host: str = "10.0.0.1"):
        headers = {"x-forwarded-for": xff} if xff else {}
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))

    def test_ignores_forwarded_header_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy_hops", 0)
        assert client_ip(self._req("1.2.3.4")) == "10.0.0.1"

    def test_uses_rightmost_hop_behind_one_proxy(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
        # leftmost entry is attacker-controlled; rightmost is what nginx saw
        assert client_ip(self._req("6.6.6.6, 203.0.113.9")) == "203.0.113.9"

    def test_falls_back_when_header_missing(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
        assert client_ip(self._req(None)) == "10.0.0.1"


class TestMiddlewareStack:
    def test_rate_limit_returns_429_with_retry_after(self):
        resp = None
        for _ in range(61):
            resp = client.post("/api/discover", json={})  # 422 is fine, only counting hits
        assert resp.status_code == 429
        assert int(resp.headers["retry-after"]) >= 1
        assert "discovery" in resp.json()["detail"]

    def test_rate_limit_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_enabled", False)
        for _ in range(65):
            resp = client.post("/api/discover", json={})
        assert resp.status_code != 429

    @pytest.mark.parametrize("path", ["/.env", "/.git/config", "/wp-login.php", "/redirect"])
    def test_scanner_probes_get_bare_404(self, path):
        resp = client.get(path)
        assert resp.status_code == 404
        assert resp.content == b""

    def test_scanner_block_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "block_scanner_paths", False)
        resp = client.get("/.env")
        assert resp.status_code == 404
        assert resp.content != b""  # normal FastAPI JSON 404

    def test_security_headers_present(self):
        h = client.get("/health").headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "SAMEORIGIN"
        assert "geolocation=(self)" in h["permissions-policy"]
        assert "frame-ancestors" in h["content-security-policy"]

    def test_csp_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "csp", "")
        assert "content-security-policy" not in client.get("/health").headers

    def test_wildcard_cors_has_no_credentials(self):
        resp = client.get("/health", headers={"Origin": "https://example.org"})
        assert resp.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in resp.headers


class TestMiscEndpoints:
    def test_version(self):
        data = client.get("/version").json()
        assert data["name"] == "terra-incognita"
        assert data["version"]

    def test_robots_hides_api(self):
        resp = client.get("/robots.txt")
        assert resp.status_code == 200
        assert "Disallow: /api/" in resp.text

    def test_favicon(self):
        resp = client.get("/favicon.ico")
        assert resp.status_code in (200, 204)
        if resp.status_code == 200:
            assert resp.headers["content-type"].startswith("image/svg+xml")
