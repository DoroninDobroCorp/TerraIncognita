"""Map configuration and tile service endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/map", tags=["map"])


class TileSource(BaseModel):
    name: str
    url: str
    attribution: str
    type: str = "raster"
    tile_size: int = 256


class CategoryStyle(BaseModel):
    icon: str
    label: str
    color: str
    group: str


class MapConfig(BaseModel):
    """Map configuration for the client."""

    default_center: list[float] = [18.7712, 42.4410]  # [lng, lat] Kotor
    default_zoom: int = 13
    min_zoom: int = 3
    max_zoom: int = 19
    tile_sources: dict[str, TileSource] = {}
    category_styles: dict[str, CategoryStyle] = {}


# Prebuilt config
_TILE_SOURCES = {
    "dark": TileSource(
        name="Terra Dark",
        url="https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors, © CARTO",
        tile_size=256,
    ),
    "satellite": TileSource(
        name="Satellite",
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attribution="© Esri, Maxar, Earthstar Geographics",
    ),
    "terrain": TileSource(
        name="Terrain",
        url="https://tile.opentopomap.org/{z}/{x}/{y}.png",
        attribution="© OpenTopoMap contributors, © OpenStreetMap",
        tile_size=256,
    ),
}

_CATEGORY_STYLES = {
    "abandoned": CategoryStyle(icon="🏚️", label="Abandoned", color="#e74c3c", group="unusual"),
    "underground": CategoryStyle(icon="🕳️", label="Underground", color="#8e44ad", group="unusual"),
    "industrial": CategoryStyle(icon="🏭", label="Industrial", color="#d35400", group="unusual"),
    "ruins": CategoryStyle(icon="🏛️", label="Ruins", color="#c0392b", group="unusual"),
    "military": CategoryStyle(icon="🎖️", label="Military", color="#2c3e50", group="unusual"),
    "nature_hidden": CategoryStyle(icon="🌿", label="Hidden Nature", color="#27ae60", group="unusual"),
    "viewpoint": CategoryStyle(icon="👁️", label="Viewpoint", color="#2980b9", group="classic"),
    "street_art": CategoryStyle(icon="🎨", label="Street Art", color="#e91e63", group="unusual"),
    "transport": CategoryStyle(icon="🚂", label="Transport", color="#f39c12", group="unusual"),
    "water": CategoryStyle(icon="💧", label="Water", color="#3498db", group="unusual"),
    "cave": CategoryStyle(icon="🦇", label="Cave", color="#34495e", group="unusual"),
    "religious": CategoryStyle(icon="⛪", label="Religious", color="#9b59b6", group="classic"),
    "landmark": CategoryStyle(icon="🗿", label="Landmark", color="#e67e22", group="classic"),
    "museum": CategoryStyle(icon="🏛️", label="Museum", color="#1abc9c", group="classic"),
    "architecture": CategoryStyle(icon="🏰", label="Architecture", color="#16a085", group="classic"),
    "monument": CategoryStyle(icon="🗽", label="Monument", color="#f1c40f", group="classic"),
    "park": CategoryStyle(icon="🌳", label="Park", color="#2ecc71", group="classic"),
    "restaurant_notable": CategoryStyle(icon="🍽️", label="Outstanding Restaurant", color="#ff6b6b", group="notable"),
    "hotel_notable": CategoryStyle(icon="🏨", label="Outstanding Hotel", color="#a29bfe", group="notable"),
}


@router.get("/config")
async def get_map_config() -> dict:
    """Return map configuration including tile sources and category styles."""
    return {
        "default_center": [18.7712, 42.4410],
        "default_zoom": 13,
        "min_zoom": 3,
        "max_zoom": 19,
        "tile_sources": {k: v.model_dump() for k, v in _TILE_SOURCES.items()},
        "category_styles": {k: v.model_dump() for k, v in _CATEGORY_STYLES.items()},
    }


@router.get("/presets")
async def get_filter_presets() -> dict:
    """Return filter presets for the map UI."""
    all_cats = list(_CATEGORY_STYLES.keys())
    unusual = [k for k, v in _CATEGORY_STYLES.items() if v.group == "unusual"]
    classic = [k for k, v in _CATEGORY_STYLES.items() if v.group == "classic"]
    notable = [k for k, v in _CATEGORY_STYLES.items() if v.group == "notable"]
    return {
        "all": {"label": "🌍 All Interesting", "categories": all_cats},
        "hidden": {"label": "🕵️ Hidden Only", "categories": unusual},
        "urbex": {"label": "🏚️ Urbex", "categories": ["abandoned", "underground", "industrial", "ruins", "military"]},
        "nature": {"label": "🌿 Nature", "categories": ["nature_hidden", "viewpoint", "water", "cave", "park"]},
        "history": {"label": "🏛️ History", "categories": ["ruins", "military", "religious", "landmark", "museum", "monument", "architecture"]},
        "classic": {"label": "⭐ Classic", "categories": classic},
        "notable": {"label": "🍽️ Outstanding Places", "categories": notable},
    }


# ── Historical Map Overlay (Story 3.5) ──


class HistoricalLayer(BaseModel):
    """A historical map tile layer for time-travel overlay."""

    id: str
    name: str
    period: str
    year_start: int
    year_end: int
    url: str
    attribution: str
    type: str = "raster"
    tile_size: int = 256
    min_zoom: int = 1
    max_zoom: int = 18
    bounds: list[float] | None = Field(
        default=None,
        description="[west, south, east, north] bounding box, or null for global",
    )


_HISTORICAL_LAYERS = [
    HistoricalLayer(
        id="ohm",
        name="OpenHistoricalMap",
        period="Multi-era",
        year_start=1000,
        year_end=2000,
        url="https://tile.openhistoricalmap.org/historicmap/{z}/{x}/{y}.png",
        attribution="© OpenHistoricalMap contributors",
        tile_size=256,
        min_zoom=1,
        max_zoom=18,
    ),
    HistoricalLayer(
        id="esri_topo_1900",
        name="Esri World Topo (Historic)",
        period="Early 20th Century",
        year_start=1890,
        year_end=1940,
        url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attribution="© Esri, DeLorme, NAVTEQ",
        tile_size=256,
        min_zoom=1,
        max_zoom=16,
    ),
    HistoricalLayer(
        id="owm_1880",
        name="Old World Maps (~1880)",
        period="Late 19th Century",
        year_start=1850,
        year_end=1900,
        url="https://maps.georeferencer.com/georeferences/28da2318-c4b3-5f25-83dc-3da27859fea2/2019-02-19T17:27:12.514288Z/map/{z}/{x}/{y}.png",
        attribution="© Georeferencer, David Rumsey Map Collection",
        tile_size=256,
        min_zoom=3,
        max_zoom=15,
    ),
]


@router.get("/historical")
async def get_historical_layers() -> dict:
    """Return available historical map layers for overlay."""
    return {
        "layers": [layer.model_dump() for layer in _HISTORICAL_LAYERS],
        "default_opacity": 0.5,
        "description": "Historical map overlays for time-travel exploration. "
        "Use the opacity slider to blend between modern and historical views.",
    }
