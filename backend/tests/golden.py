"""Golden-set harness: run Overpass-style fixtures through the real discovery pipeline.

A fixture is ``tests/fixtures/overpass_<name>.json``::

    {
      "name": "...", "description": "...", "provenance": "...",
      "center": [lat, lng], "radius_km": 4.0,
      "elements": [ ...Overpass `out center tags` elements... ],
      "extra_sources": {                       # optional, other sources for the same area
        "atlas":    [ ...Atlas Obscura API items... ],
        "wikidata": [ ...SPARQL result bindings... ]
      },
      "expect": {
        "gems":    ["osm_node_123", ...],      # places a human would send you to
        "clutter": ["osm_way_456", ...],       # mainstream stuff that must rank below every gem
        "top_n": 14,                           # every gem must be inside the first top_n results
        "top3_distinct_sites": true,           # the first three results are three different sites
        "also_merged": [["osm_node_1", "atlas_9"]]   # ids that must end up as one place
      }
    }

Ids may be any member of a fused place (``osm_node_…``, ``atlas_…``, ``wiki_Q…``). Places that are
neither gem nor clutter are "don't care": they can rank anywhere.

Adding a city (needs network once, be polite to Overpass: a handful of queries, with a UA)::

    cd backend
    PYTHONPATH=. python -m tests.golden record my_city 50.06 19.94 3    # writes the skeleton
    PYTHONPATH=. python -m tests.golden rank tests/fixtures/overpass_my_city.json
    # read the table, put ids into "gems" / "clutter" in the JSON, run: pytest tests/test_golden_search.py

``rank`` prints the final order, score and reasons, i.e. exactly what ``sort_by="unusual"`` returns.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.place import DiscoverRequest, Place
from app.services.discovery import discover
from app.sources.atlas_obscura import AtlasObscuraSource
from app.sources.osm import OSMSource
from app.sources.wikidata import WikidataSource

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def fixture_paths() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("overpass_*.json"))


def load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_sources(fx: dict) -> dict[str, list[Place]]:
    """What each source would hand to the pipeline for this fixture."""
    extra = fx.get("extra_sources") or {}
    return {
        "osm": OSMSource()._parse_elements(fx["elements"]),
        "atlas": AtlasObscuraSource()._parse_api_response({"places": extra.get("atlas", [])}),
        "wiki": WikidataSource()._parse_results({"results": {"bindings": extra.get("wikidata", [])}}),
    }


async def run_pipeline(fx: dict, sort_by: str = "unusual") -> list[Place]:
    """The full ``discover()`` pipeline (fusion → classification → scoring → sort), all pages."""
    parsed = parse_sources(fx)
    mocks = {name: MagicMock(source_name=name) for name in ("osm", "atlas", "wiki", "deep")}
    for name, mock in mocks.items():
        mock.search = AsyncMock(return_value=parsed.get(name, []))
    mocks["deep"].get_status = AsyncMock(return_value=("idle", None))
    lat, lng = fx["center"]
    with patch("app.services.discovery._osm", mocks["osm"]), \
         patch("app.services.discovery._atlas", mocks["atlas"]), \
         patch("app.services.discovery._wiki", mocks["wiki"]), \
         patch("app.services.discovery._deep", mocks["deep"]):
        places: list[Place] = []
        cursor = None
        while True:
            page = await discover(DiscoverRequest(
                lat=lat, lng=lng, radius_km=fx["radius_km"], sort_by=sort_by, limit=200, cursor=cursor))
            places += page.places
            if not page.has_more:
                return places
            cursor = page.cursor


def owners(places: list[Place]) -> dict[str, Place]:
    """Every id a fused place answers to → that place."""
    out: dict[str, Place] = {}
    for p in places:
        out[p.id] = p
        for merged in p.metadata.get("merged_ids", []):
            out[merged] = p
    return out


def ranks(places: list[Place], ids: list[str]) -> dict[str, int]:
    """0-based rank of the place each id belongs to (``KeyError`` for an unknown id)."""
    by_id = owners(places)
    position = {p.id: i for i, p in enumerate(places)}
    return {i: position[by_id[i].id] for i in ids}


def ranking_table(places: list[Place], n: int = 20, mark: dict[str, str] | None = None) -> str:
    """Human-readable top-N: rank, score, name, categories, reasons (for failure messages and the CLI)."""
    mark = mark or {}
    lines = []
    for i, p in enumerate(places[:n]):
        ids = {p.id, *p.metadata.get("merged_ids", [])}
        tag = next((mark[x] for x in ids if x in mark), " ")
        lines.append(
            f"{i:3} {tag} {p.unusual_score:.3f} {p.id:24} {str(p.name)[:34]:34} "
            f"{','.join(c.value for c in p.categories)[:34]:34} {'; '.join(p.unusual_reasons)[:60]}"
        )
    return "\n".join(lines)


# ── CLI: record a new fixture / print the ranking of an existing one ──


def _record(name: str, lat: float, lng: float, radius_km: float) -> None:
    import httpx

    from app.config import settings
    from app.sources.osm import build_overpass_query
    from app.utils.geo import bounding_box

    south, west, north, east = bounding_box(lat, lng, radius_km)
    query = build_overpass_query(f"{south},{west},{north},{east}", 90, max_elements=5000)
    print(f"Querying {settings.overpass_url} …", file=sys.stderr)
    resp = httpx.post(
        settings.overpass_url, data={"data": query}, timeout=120,
        headers={"User-Agent": settings.wikimedia_user_agent + " fixture-recorder"},
    )
    resp.raise_for_status()
    data = resp.json()
    skeleton = {
        "name": name,
        "description": "TODO: what makes this area a good test",
        "provenance": f"Recorded from {settings.overpass_url} (centre {lat},{lng}, radius {radius_km} km, "
                      f"{len(data.get('elements', []))} elements, ODbL).",
        "center": [lat, lng], "radius_km": radius_km,
        "elements": data.get("elements", []),
        "extra_sources": {},
        "expect": {"gems": [], "clutter": [], "top_n": 12, "top3_distinct_sites": True},
    }
    target = FIXTURE_DIR / f"overpass_{name}.json"
    target.write_text(json.dumps(skeleton, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(skeleton['elements'])} elements); now run: python -m tests.golden rank {target}")


def _rank(path: str, n: int) -> None:
    fx = load_fixture(Path(path))
    expect = fx.get("expect", {})
    mark = {**dict.fromkeys(expect.get("gems", []), "G"), **dict.fromkeys(expect.get("clutter", []), "x")}
    print(ranking_table(asyncio.run(run_pipeline(fx)), n, mark))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["record"] and len(args) == 5:
        _record(args[1], float(args[2]), float(args[3]), float(args[4]))
    elif args[:1] == ["rank"] and len(args) in (2, 3):
        _rank(args[1], int(args[2]) if len(args) == 3 else 40)
    else:
        print(__doc__)
