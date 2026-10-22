"""NaN / Infinity in JSON bodies must be a clean 422, never a 500."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
JSON = {"content-type": "application/json"}

CASES = [
    ("/api/visits", '{"place_id":"p","lat":NaN,"lng":1}'),
    ("/api/visits", '{"place_id":"p","lat":1,"lng":Infinity}'),
    ("/api/visits", '{"place_id":"p","lat":1,"lng":1,"duration_minutes":Infinity}'),
    ("/api/visits/proximity", '{"lat":NaN,"lng":1}'),
    ("/api/fog/reveal", '{"points":[{"lat":NaN,"lng":1}]}'),
    ("/api/fog/region", '{"lat":1,"lng":1,"radius_km":Infinity}'),
    ("/api/route", '{"origin":{"lat":NaN,"lng":1}}'),
    ("/api/route", '{"origin":{"lat":1,"lng":1},"max_duration_hours":NaN}'),
    ("/api/route/explore", '{"origin":{"lat":1,"lng":Infinity}}'),
    ("/api/chat", '{"message":"hi","lat":NaN,"lng":1}'),
    ("/api/describe", '{"place_id":"p","lat":NaN,"lng":1}'),
    ("/api/recommend", '{"lat":1,"lng":NaN}'),
    ("/api/story", '{"place_id":"p","lat":Infinity,"lng":1}'),
    ("/api/offline/tiles/download",
     '{"name":"n","bbox":{"min_lat":NaN,"min_lng":0,"max_lat":1,"max_lng":1}}'),
    ("/api/offline/navigate",
     '{"current_position":{"lat":NaN,"lng":1},"target_coordinates":{"lat":1,"lng":1}}'),
    ("/api/community/places", '{"name":"n","lat":NaN,"lng":1,"author_id":"a"}'),
]


@pytest.mark.parametrize(("path", "body"), CASES)
def test_non_finite_numbers_are_a_clean_422(path, body):
    resp = client.post(path, content=body, headers=JSON)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail and all({"loc", "msg", "type"} <= set(e) for e in detail)


def test_422_does_not_echo_the_offending_input():
    resp = client.post("/api/visits", json={"place_id": "p", "lat": "<script>x</script>", "lng": 1})
    assert resp.status_code == 422
    assert "<script>" not in resp.text


def test_valid_requests_are_unaffected():
    resp = client.post("/api/visits/proximity", json={"lat": 42.4, "lng": 18.7})
    assert resp.status_code == 200
