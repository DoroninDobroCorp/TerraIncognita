# Terra Incognita — Backend (Discovery Engine)

## Quick Start

```bash
# Install
cd backend
pip install -e ".[dev]"

# Run
uvicorn app.main:app --reload

# Test
pytest tests/ -v
```

## API

### POST /api/discover

Find interesting places near a location.

```json
{
  "lat": 42.45,
  "lng": 18.53,
  "radius_km": 5.0,
  "categories": ["abandoned", "military"],
  "exclude_visited": [],
  "limit": 50,
  "sort_by": "confidence",
  "cursor": null
}
```

Response:
```json
{
  "places": [
    {
      "id": "osm_node_12345",
      "source": "osm",
      "sources": ["osm", "atlas"],
      "name": "Old Bunker",
      "description": "A WWII military bunker",
      "categories": ["military", "underground"],
      "coordinates": {"lat": 42.45, "lng": 18.53},
      "confidence": 0.85,
      "distance_m": 120.5,
      "tags": ["military=bunker"],
      "photos": ["https://..."],
      "metadata": {}
    }
  ],
  "total": 42,
  "has_more": true,
  "cursor": "eyI..."
}
```

### GET /health

Health check endpoint.

## Configuration

Set via environment variables (prefix `TERRA_`) or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `TERRA_OVERPASS_URL` | `https://overpass-api.de/api/interpreter` | Overpass API endpoint |
| `TERRA_CACHE_DIR` | `cache` | Cache directory path |
| `TERRA_CACHE_TTL_SECONDS` | `86400` | Cache TTL (24h) |
| `TERRA_ANTHROPIC_API_KEY` | — | For LLM classification (optional) |
| `TERRA_DEFAULT_RADIUS_KM` | `5.0` | Default search radius |
| `TERRA_MAX_RADIUS_KM` | `50.0` | Maximum allowed radius |

## Architecture

```
app/
├── main.py           # FastAPI app
├── config.py         # Settings
├── models/place.py   # Place, PlaceCategory, API models
├── sources/          # Data source adapters
│   ├── base.py       # Abstract base
│   ├── osm.py        # OpenStreetMap/Overpass
│   ├── atlas_obscura.py  # Atlas Obscura
│   └── wikidata.py   # Wikidata SPARQL
├── services/
│   ├── discovery.py  # Orchestrator
│   ├── fusion.py     # Deduplication & merge
│   ├── classifier.py # Category classification
│   └── cache.py      # Disk cache
└── utils/
    ├── geo.py        # Haversine, bounding box
    └── rate_limiter.py
```

## Docker

```bash
docker build -t terra-backend .
docker run -p 8000:8000 terra-backend
```
