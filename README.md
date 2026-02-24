# 🌍 Terra Incognita

**AI-powered exploration companion** — discover hidden, unusual, and forgotten places around you.

Terra Incognita combines open geodata (OpenStreetMap, Wikidata, Atlas Obscura) with LLM intelligence to help travelers and urban explorers find places that don't appear in mainstream guides.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

---

## ✨ Features

- **🔍 Smart Discovery** — finds abandoned buildings, military objects, underground structures, historical ruins and other unusual places nearby
- **🤖 AI Classification** — LLM-powered categorization and description of places with contextual storytelling
- **🗺️ Interactive Map** — MapLibre GL-based explorer with fog-of-war mechanics
- **📖 Travel Journal** — automatic visit logging with proximity detection
- **🏆 Gamification** — XP, achievements, and fog-of-war map revealing
- **🧭 Route Builder** — generates exploration routes with corridor-based discovery
- **💬 AI Chat** — conversational assistant for trip planning and place recommendations
- **👥 Community** — share routes, places, and reviews with other explorers
- **📡 Offline Mode** — download regions for offline exploration

## 🏗️ Architecture

```
travel-assistant/
├── backend/              # FastAPI backend (Discovery Engine)
│   ├── app/
│   │   ├── api/          # REST API endpoints
│   │   ├── models/       # Pydantic data models
│   │   ├── services/     # Business logic & LLM integration
│   │   ├── sources/      # Data source adapters (OSM, Wikidata, Atlas Obscura)
│   │   └── utils/        # Helpers (geo, rate limiting, HTTP)
│   └── tests/            # Pytest test suite
├── map/                  # Frontend — interactive exploration map
│   ├── app.html          # Main application UI
│   └── explorer.html     # Explorer map view
└── data/                 # GeoJSON & static geodata
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- (Optional) API keys for LLM features: OpenAI or Anthropic

### Installation

```bash
cd backend
pip install -e ".[dev]"
```

### Configuration

```bash
cp backend/.env.example backend/.env
# Edit .env and add your API keys (optional — core discovery works without LLM)
```

| Variable | Description |
|----------|-------------|
| `TERRA_OPENAI_API_KEY` | OpenAI API key for AI features |
| `TERRA_ANTHROPIC_API_KEY` | Anthropic API key (alternative) |
| `TERRA_LLM_PROVIDER` | `openai` or `anthropic` |
| `TERRA_GEMINI_API_KEY` | Google Gemini key for deep research |

See [`backend/.env.example`](backend/.env.example) for all available options.

### Run

```bash
cd backend
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`. Open `http://localhost:8000/map/app.html` for the interactive map.

### Run Tests

```bash
cd backend
pytest tests/ -v
```

## 🐳 Docker

```bash
cd backend
docker build -t terra-incognita .
docker run -p 8000:8000 --env-file .env terra-incognita
```

## 📡 API Overview

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/discover` | POST | Find interesting places near coordinates |
| `/api/route` | POST | Build an exploration route |
| `/api/chat` | POST | AI chat for trip planning |
| `/api/describe` | POST | Generate AI description for a place |
| `/api/recommend` | POST | Get personalized recommendations |
| `/api/story` | POST | Generate storytelling narrative |
| `/api/visits` | GET/POST | Travel journal entries |
| `/api/fog/reveal` | POST | Reveal fog-of-war on the map |
| `/api/community/places` | GET/POST | Community-shared places |
| `/health` | GET | Health check & LLM status |

## 🛠️ Tech Stack

- **Backend**: Python, FastAPI, Pydantic, httpx
- **Frontend**: Vanilla JS, MapLibre GL JS
- **Data Sources**: OpenStreetMap Overpass API, Wikidata SPARQL, Atlas Obscura
- **AI/LLM**: OpenAI GPT / Anthropic Claude (pluggable)
- **Caching**: File-based with configurable TTL

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
