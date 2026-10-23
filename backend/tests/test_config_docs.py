"""The shipped .env.example and the docs must describe settings that really exist."""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / "backend" / ".env.example"
FIELDS = {name.upper() for name in Settings.model_fields}


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


def test_max_radius_and_limit_settings_bound_every_schema_that_has_them():
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
        "params = {p['name']: p['schema'].get('maximum') for path in ('/api/daily', '/api/collections')"
        " for p in spec['paths'][path]['get']['parameters'] if p['name'] == 'radius_km'};"
        "print(json.dumps({'radius': radius, 'limit': limit, 'params': params}))"
    )
    env = {**os.environ, "TERRA_MAX_RADIUS_KM": "20", "TERRA_MAX_LIMIT": "75", "PYTHONPATH": str(ROOT / "backend")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT / "backend", timeout=120)
    assert out.returncode == 0, out.stderr
    found = json.loads(out.stdout.strip().splitlines()[-1])
    assert found["radius"], "no schema with radius_km found: the probe is broken"
    assert set(found["radius"].values()) == {20.0}, found["radius"]
    assert set(found["params"].values()) == {20.0}, found["params"]
    assert set(found["limit"].values()) == {75}, found["limit"]
