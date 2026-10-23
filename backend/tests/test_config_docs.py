"""The shipped .env.example and the docs must describe settings that really exist."""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / "backend" / ".env.example"
FIELDS = {name.upper() for name in Settings.model_fields}


def _sandbox(tmp_path) -> dict[str, str]:
    """Environment for a child process that imports the app: its caches and stores go into ``tmp_path``
    (imports create ``cache/`` and ``data/`` directories relative to the working directory otherwise)."""
    return {
        "TERRA_CACHE_DIR": str(tmp_path / "cache"), "TERRA_JOURNAL_DATA_DIR": str(tmp_path / "journal"),
        "TERRA_GAMIFICATION_DATA_DIR": str(tmp_path / "gamification"),
        "TERRA_COMMUNITY_DATA_DIR": str(tmp_path / "community"), "TERRA_OFFLINE_DATA_DIR": str(tmp_path / "offline"),
    }


def _assignments() -> list[tuple[str, str]]:
    """(KEY, raw value) for every `KEY=value` line, commented-out or not."""
    return re.findall(r"^#?\s*TERRA_([A-Z0-9_]+)=(.*)$", ENV_EXAMPLE.read_text(), flags=re.M)


def test_every_documented_key_is_a_real_setting():
    unknown = [key for key, _ in _assignments() if key not in FIELDS]
    assert not unknown, f".env.example documents settings that do not exist: {unknown}"


def test_no_inline_comments_after_values():
    """systemd EnvironmentFile= and docker --env-file keep a trailing '# ...' as part of the value."""
    bad = [(key, val) for key, val in _assignments() if re.search(r"\s#", val)]
    assert not bad, f"inline comments would end up inside the values: {bad}"


def test_uncommented_example_loads_cleanly(tmp_path, monkeypatch):
    """Uncomment every assignment, parse it the way systemd would (no comment stripping), and load Settings."""
    for key, val in _assignments():
        monkeypatch.setenv(f"TERRA_{key}", val)
    monkeypatch.chdir(tmp_path)  # no stray .env
    settings = Settings(_env_file=None)
    assert settings.llm_daily_token_cap == 500000
    assert settings.llm_effort == "low"
    assert settings.csp == "default"


def test_documented_names_in_markdown_exist():
    names: set[str] = set()
    for md in ROOT.rglob("*.md"):
        if ".git" in md.parts or "node_modules" in md.parts:
            continue
        names |= set(re.findall(r"TERRA_([A-Z][A-Z0-9_]{3,})", md.read_text(encoding="utf-8")))
    unknown = sorted(n for n in names if n not in FIELDS and not n.endswith("_"))
    assert not unknown, f"docs mention settings that do not exist: {unknown}"


def test_max_radius_and_limit_settings_bound_every_schema_that_has_them(tmp_path):
    """TERRA_MAX_RADIUS_KM / TERRA_MAX_LIMIT used to bind only DiscoverRequest; other endpoints hard-coded 50/200."""
    import json
    import os
    import subprocess
    import sys

    code = (
        "import json; from app.main import app; spec = app.openapi();"
        "radius = {}; limit = {};"
        "[radius.update({name: s['properties']['radius_km'].get('maximum')}) for name, s in spec['components']['schemas'].items()"
        " if 'radius_km' in s.get('properties', {}) and not name.startswith('FogRegion')];"  # fog stats: no upstream query
        "[limit.update({name: s['properties']['limit'].get('maximum')}) for name, s in spec['components']['schemas'].items()"
        " if name == 'DiscoverRequest' and 'limit' in s.get('properties', {})];"
        "params = {path: p['schema'].get('maximum') for path in ('/api/daily', '/api/collections')"
        " for p in spec['paths'][path]['get']['parameters'] if p['name'] == 'radius_km'};"
        "print(json.dumps({'radius': radius, 'limit': limit, 'params': params}))"
    )
    env = {**os.environ, **_sandbox(tmp_path), "TERRA_MAX_RADIUS_KM": "20", "TERRA_MAX_LIMIT": "75", "PYTHONPATH": str(ROOT / "backend")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT / "backend", timeout=120)
    assert out.returncode == 0, out.stderr
    found = json.loads(out.stdout.strip().splitlines()[-1])
    assert found["radius"], "no schema with radius_km found: the probe is broken"
    assert set(found["radius"].values()) == {20.0}, found["radius"]
    assert found["params"]["/api/daily"] == 20.0, found["params"]
    assert found["params"]["/api/collections"] == 50.0  # always accepted: clamped to the cap inside
    assert set(found["limit"].values()) == {75}, found["limit"]


_LIMIT_PROBE = r"""import asyncio, json
from app.models.place import DiscoverResponse, Coordinates
captured, errors = [], {}
async def fake(req):
    captured.append(req.limit)
    return DiscoverResponse(places=[], total=0, has_more=False)
async def main():
    from app.services import daily_place, loop_route, place_collections, route_builder
    from app.models.loop_route import LoopRouteRequest
    from app.models.route import ExploreRouteRequest, RouteRequest
    for mod in (daily_place, loop_route, place_collections, route_builder):
        mod.discover = fake
    calls = {
        "daily": lambda: daily_place.get_daily_place(42.4, 18.5),
        "loop": lambda: loop_route.build_loop_route(LoopRouteRequest(lat=42.4, lng=18.5, duration_hours=2)),
        "collections": lambda: place_collections.build_collections(42.4, 18.5, 10),
        "explore": lambda: route_builder.build_explore_route(ExploreRouteRequest(origin=Coordinates(lat=42.4, lng=18.5))),
        "route": lambda: route_builder.build_route(RouteRequest(origin=Coordinates(lat=42.4, lng=18.5), destination=Coordinates(lat=42.45, lng=18.55))),
    }
    for name, call in calls.items():
        try:
            await call()
        except Exception as exc:
            errors[name] = type(exc).__name__
asyncio.run(main())
print(json.dumps({"captured": captured, "errors": errors}))
"""


def test_internal_discovery_callers_stay_within_a_lowered_max_limit_and_radius(tmp_path):
    """daily, loop, collections and the route builders used to ask discovery for 200 places regardless of
    TERRA_MAX_LIMIT: DiscoverRequest then raised, and the endpoints answered 500."""
    import json
    import os
    import subprocess
    import sys

    env = {**os.environ, **_sandbox(tmp_path), "TERRA_MAX_LIMIT": "75", "TERRA_MAX_RADIUS_KM": "20", "PYTHONPATH": str(ROOT / "backend")}
    out = subprocess.run([sys.executable, "-c", _LIMIT_PROBE], capture_output=True, text=True, env=env,
                         cwd=ROOT / "backend", timeout=120)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["captured"] and set(result["captured"]) == {75}, result
    # only the expected "nothing around here" domain errors, never a validation error from the request model
    assert set(result["errors"].values()) <= {"NoPlacesNearbyError", "NotEnoughPlacesError"}, result


def test_default_radii_never_exceed_a_lowered_max_radius(tmp_path):
    """A default of 5 km with TERRA_MAX_RADIUS_KM=3 made chat, recommend and explore fail on their own default."""
    import json
    import os
    import subprocess
    import sys

    code = (
        "import json;"
        "from app.models.chat import ChatRequest, RecommendationRequest;"
        "from app.models.place import DiscoverRequest;"
        "from app.models.route import ExploreRouteRequest;"
        "from app.models.place import Coordinates;"
        "r = {"
        "'chat': ChatRequest(message='x', lat=1, lng=1).radius_km,"
        "'recommend': RecommendationRequest(lat=1, lng=1).radius_km,"
        "'explore': ExploreRouteRequest(origin=Coordinates(lat=1, lng=1)).radius_km,"
        "'discover': DiscoverRequest(lat=1, lng=1).radius_km};"
        "print(json.dumps(r))"
    )
    env = {**os.environ, **_sandbox(tmp_path), "TERRA_MAX_RADIUS_KM": "3", "PYTHONPATH": str(ROOT / "backend")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=ROOT / "backend", timeout=120)
    assert out.returncode == 0, out.stderr
    radii = json.loads(out.stdout.strip().splitlines()[-1])
    assert set(radii) == {"chat", "recommend", "explore", "discover"}
    assert all(0 < r <= 3 for r in radii.values()), radii
