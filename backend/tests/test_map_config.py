"""Tests for map configuration API endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.anyio
async def test_get_map_config(client: AsyncClient):
    resp = await client.get("/api/map/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "default_center" in data
    assert "tile_sources" in data
    assert "category_styles" in data
    assert len(data["default_center"]) == 2
    assert "dark" in data["tile_sources"]
    assert "satellite" in data["tile_sources"]
    assert "terrain" in data["tile_sources"]
    # All 19 categories should be present (11 unusual + 6 classic + 2 notable)
    assert len(data["category_styles"]) == 19
    for cat_name, cat_style in data["category_styles"].items():
        assert "icon" in cat_style
        assert "label" in cat_style
        assert "color" in cat_style
        assert "group" in cat_style
        assert cat_style["group"] in ("unusual", "classic", "notable")


@pytest.mark.anyio
async def test_get_filter_presets(client: AsyncClient):
    resp = await client.get("/api/map/presets")
    assert resp.status_code == 200
    data = resp.json()
    expected_presets = {"all", "hidden", "urbex", "nature", "history", "classic", "notable"}
    assert set(data.keys()) == expected_presets
    for preset_name, preset_data in data.items():
        assert "label" in preset_data
        assert "categories" in preset_data
        assert isinstance(preset_data["categories"], list)
        assert len(preset_data["categories"]) > 0


@pytest.mark.anyio
async def test_map_config_tile_sources_have_urls(client: AsyncClient):
    resp = await client.get("/api/map/config")
    data = resp.json()
    for source_name, source in data["tile_sources"].items():
        assert "url" in source
        assert source["url"].startswith("https://")
        assert "attribution" in source


@pytest.mark.anyio
async def test_urbex_preset_categories(client: AsyncClient):
    resp = await client.get("/api/map/presets")
    data = resp.json()
    urbex = data["urbex"]["categories"]
    assert "abandoned" in urbex
    assert "underground" in urbex
    assert "industrial" in urbex
    assert "ruins" in urbex
    assert "military" in urbex
    # Should not contain classic categories
    assert "museum" not in urbex
    assert "park" not in urbex


@pytest.mark.anyio
async def test_all_preset_covers_all_categories(client: AsyncClient):
    config_resp = await client.get("/api/map/config")
    presets_resp = await client.get("/api/map/presets")
    all_cats = set(config_resp.json()["category_styles"].keys())
    preset_cats = set(presets_resp.json()["all"]["categories"])
    assert all_cats == preset_cats, "All preset must include every category"


@pytest.mark.anyio
async def test_hidden_preset_only_unusual(client: AsyncClient):
    config_resp = await client.get("/api/map/config")
    presets_resp = await client.get("/api/map/presets")
    styles = config_resp.json()["category_styles"]
    hidden_cats = presets_resp.json()["hidden"]["categories"]
    for cat in hidden_cats:
        assert styles[cat]["group"] == "unusual", f"{cat} should be unusual"


@pytest.mark.anyio
async def test_classic_preset_only_classic(client: AsyncClient):
    config_resp = await client.get("/api/map/config")
    presets_resp = await client.get("/api/map/presets")
    styles = config_resp.json()["category_styles"]
    classic_cats = presets_resp.json()["classic"]["categories"]
    for cat in classic_cats:
        assert styles[cat]["group"] == "classic", f"{cat} should be classic"


@pytest.mark.anyio
async def test_category_colors_are_hex(client: AsyncClient):
    resp = await client.get("/api/map/config")
    for cat, style in resp.json()["category_styles"].items():
        assert style["color"].startswith("#"), f"{cat} color should be hex"


@pytest.mark.anyio
async def test_default_center_is_valid_coordinates(client: AsyncClient):
    resp = await client.get("/api/map/config")
    center = resp.json()["default_center"]
    lng, lat = center
    assert -180 <= lng <= 180, "longitude out of range"
    assert -90 <= lat <= 90, "latitude out of range"


# ── Historical Map Overlay (Story 3.5) ──


@pytest.mark.anyio
async def test_get_historical_layers(client: AsyncClient):
    resp = await client.get("/api/map/historical")
    assert resp.status_code == 200
    data = resp.json()
    assert "layers" in data
    assert "default_opacity" in data
    assert isinstance(data["layers"], list)
    assert len(data["layers"]) >= 3
    assert 0 <= data["default_opacity"] <= 1


@pytest.mark.anyio
async def test_historical_layers_have_required_fields(client: AsyncClient):
    resp = await client.get("/api/map/historical")
    for layer in resp.json()["layers"]:
        assert "id" in layer
        assert "name" in layer
        assert "period" in layer
        assert "year_start" in layer
        assert "year_end" in layer
        assert "url" in layer
        assert "attribution" in layer
        assert layer["year_start"] < layer["year_end"]


@pytest.mark.anyio
async def test_historical_layers_have_valid_urls(client: AsyncClient):
    resp = await client.get("/api/map/historical")
    for layer in resp.json()["layers"]:
        assert layer["url"].startswith("https://"), f"{layer['id']} URL should be HTTPS"
        assert "{z}" in layer["url"], f"{layer['id']} URL should have zoom placeholder"
        assert "{x}" in layer["url"], f"{layer['id']} URL should have x placeholder"
        assert "{y}" in layer["url"], f"{layer['id']} URL should have y placeholder"


@pytest.mark.anyio
async def test_historical_layer_ids_unique(client: AsyncClient):
    resp = await client.get("/api/map/historical")
    ids = [l["id"] for l in resp.json()["layers"]]
    assert len(ids) == len(set(ids)), "Layer IDs must be unique"


@pytest.mark.anyio
async def test_historical_layers_cover_multiple_periods(client: AsyncClient):
    resp = await client.get("/api/map/historical")
    periods = {l["period"] for l in resp.json()["layers"]}
    assert len(periods) >= 2, "Should cover at least 2 different historical periods"
