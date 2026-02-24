🇬🇧 [English](#-english) | 🇷🇺 [Русский](#-русский)

---

# 🇬🇧 English

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

---

# 🇷🇺 Русский

# 🌍 Terra Incognita

**Исследовательский помощник на базе ИИ** — открывайте скрытые, необычные и забытые места вокруг вас.

Terra Incognita объединяет открытые геоданные (OpenStreetMap, Wikidata, Atlas Obscura) с возможностями LLM, чтобы помочь путешественникам и городским исследователям находить места, которых нет в популярных путеводителях.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

---

## ✨ Возможности

- **🔍 Умный поиск** — находит заброшенные здания, военные объекты, подземные сооружения, исторические руины и другие необычные места поблизости
- **🤖 ИИ-классификация** — категоризация и описание мест с помощью LLM, включая контекстный сторителлинг
- **🗺️ Интерактивная карта** — обозреватель на базе MapLibre GL с механикой «тумана войны»
- **📖 Дневник путешествий** — автоматическая фиксация посещений с определением близости к объекту
- **🏆 Геймификация** — очки опыта, достижения и постепенное открытие карты из-под «тумана войны»
- **🧭 Построение маршрутов** — генерация исследовательских маршрутов с обнаружением мест вдоль коридора движения
- **💬 ИИ-чат** — разговорный помощник для планирования поездок и рекомендаций по местам
- **👥 Сообщество** — делитесь маршрутами, местами и отзывами с другими исследователями
- **📡 Офлайн-режим** — загрузка регионов для исследования без интернета

## 🏗️ Архитектура

```
travel-assistant/
├── backend/              # FastAPI-бэкенд (Discovery Engine)
│   ├── app/
│   │   ├── api/          # Эндпоинты REST API
│   │   ├── models/       # Модели данных Pydantic
│   │   ├── services/     # Бизнес-логика и интеграция с LLM
│   │   ├── sources/      # Адаптеры источников данных (OSM, Wikidata, Atlas Obscura)
│   │   └── utils/        # Утилиты (гео, rate limiting, HTTP)
│   └── tests/            # Набор тестов Pytest
├── map/                  # Фронтенд — интерактивная карта исследований
│   ├── app.html          # Основной интерфейс приложения
│   └── explorer.html     # Карта обозревателя
└── data/                 # GeoJSON и статические геоданные
```

## 🚀 Быстрый старт

### Требования

- Python 3.11+
- (Опционально) API-ключи для функций ИИ: OpenAI или Anthropic

### Установка

```bash
cd backend
pip install -e ".[dev]"
```

### Настройка

```bash
cp backend/.env.example backend/.env
# Отредактируйте .env и добавьте API-ключи (опционально — базовый поиск работает без LLM)
```

| Переменная | Описание |
|------------|----------|
| `TERRA_OPENAI_API_KEY` | API-ключ OpenAI для функций ИИ |
| `TERRA_ANTHROPIC_API_KEY` | API-ключ Anthropic (альтернативный) |
| `TERRA_LLM_PROVIDER` | `openai` или `anthropic` |
| `TERRA_GEMINI_API_KEY` | Ключ Google Gemini для глубокого анализа |

Все доступные параметры описаны в [`backend/.env.example`](backend/.env.example).

### Запуск

```bash
cd backend
uvicorn app.main:app --reload
```

API будет доступен по адресу `http://localhost:8000`. Откройте `http://localhost:8000/map/app.html` для интерактивной карты.

### Запуск тестов

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

## 📡 Обзор API

| Эндпоинт | Метод | Описание |
|----------|-------|----------|
| `/api/discover` | POST | Поиск интересных мест рядом с координатами |
| `/api/route` | POST | Построение исследовательского маршрута |
| `/api/chat` | POST | ИИ-чат для планирования поездок |
| `/api/describe` | POST | Генерация ИИ-описания места |
| `/api/recommend` | POST | Персонализированные рекомендации |
| `/api/story` | POST | Генерация сторителлинг-нарратива |
| `/api/visits` | GET/POST | Записи дневника путешествий |
| `/api/fog/reveal` | POST | Открытие «тумана войны» на карте |
| `/api/community/places` | GET/POST | Места, добавленные сообществом |
| `/health` | GET | Проверка состояния и статус LLM |

## 🛠️ Технологический стек

- **Бэкенд**: Python, FastAPI, Pydantic, httpx
- **Фронтенд**: Vanilla JS, MapLibre GL JS
- **Источники данных**: OpenStreetMap Overpass API, Wikidata SPARQL, Atlas Obscura
- **ИИ/LLM**: OpenAI GPT / Anthropic Claude (подключаемые)
- **Кэширование**: файловое с настраиваемым TTL

## 🤝 Участие в разработке

1. Сделайте форк репозитория
2. Создайте ветку для новой функциональности (`git checkout -b feature/amazing-feature`)
3. Зафиксируйте изменения (`git commit -m 'Add amazing feature'`)
4. Отправьте ветку в удалённый репозиторий (`git push origin feature/amazing-feature`)
5. Откройте Pull Request

## 📄 Лицензия

Проект распространяется под лицензией MIT — подробности в файле [LICENSE](LICENSE).
