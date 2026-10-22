"""All feature routes are registered and the OpenAPI schema (response models) builds."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

EXPECTED = {
    ("get", "/api/daily"),
    ("post", "/api/route/loop"),
    ("get", "/api/collections"),
    ("get", "/api/streak"),
    ("post", "/api/place/ask"),
    ("post", "/api/story/walk"),
}


def test_openapi_lists_the_new_endpoints():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    found = {(method, path) for path, ops in paths.items() for method in ops}
    assert EXPECTED <= found


def test_existing_route_paths_are_still_there():
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/api/route", "/api/route/explore", "/api/route/export", "/api/route/reorder",
                 "/api/story", "/api/story/route", "/api/discover", "/api/visits"):
        assert path in paths


def test_loop_and_daily_document_their_error_shapes():
    paths = client.get("/openapi.json").json()["paths"]
    assert "422" in paths["/api/route/loop"]["post"]["responses"]
    daily_params = {p["name"] for p in paths["/api/daily"]["get"]["parameters"]}
    assert daily_params == {"lat", "lng", "radius_km", "lang"}
    streak_params = {p["name"] for p in paths["/api/streak"]["get"]["parameters"]}
    assert streak_params == {"tz_offset_minutes"}
