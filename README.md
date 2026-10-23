🇬🇧 [English](#-english) | 🇷🇺 [Русский](#-русский)

---

# 🇬🇧 English

# 🌍 Terra Incognita

**AI-powered exploration companion** — discover hidden, unusual, and forgotten places around you.

Terra Incognita combines open geodata (OpenStreetMap, Wikidata, Wikipedia/Commons) and, optionally, Atlas Obscura listings with LLM intelligence to help travelers and urban explorers find places that don't appear in mainstream guides.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

---

## ✨ Features

- **🔍 Smart Discovery** — abandoned buildings, military objects, underground structures, ruins and other unusual places nearby, ranked by an explainable **unusualness score** ("abandoned · not linked to Wikipedia · listed on Atlas Obscura")
- **📖 Rich place cards** — Wikipedia summaries and attributed Wikimedia Commons photos, loaded on demand
- **🌅 Place of the day** — one hand-picked unusual spot near you, with a short story
- **🧭 Routes** — point-to-point, circular walks, and **loops of N hours** on foot or by bike; GPX/KML export
- **🗺️ Interactive Map** — MapLibre GL with fog of war, a life heatmap and light / dark / high-contrast ("sun") themes
- **📖 Travel Journal** — visit logging with proximity detection, trips, notes, exports
- **🏆 Gamification** — XP, achievements, fog-of-war reveal, **collections** ("10/47 abandoned") and day **streaks**
- **💬 AI Chat & "Ask this place"** — find places by mood, ask a question about a specific spot (voice input/output optional)
- **👥 Community** — share places, routes and reviews
- **📡 Installable & offline** — PWA with an offline shell and bounded tile cache
- **🛡️ Safe by default** — per-IP rate limits, strict CSP, hardened inputs, and an optional daily LLM token cap

## 🏗️ Architecture

```
TerraIncognita/
├── backend/              # FastAPI backend (Discovery Engine)
│   ├── app/
│   │   ├── api/          # Thin REST routers
│   │   ├── models/       # Pydantic models
│   │   ├── services/     # Discovery pipeline, scoring, routes, journal, LLM features
│   │   ├── sources/      # OSM, Wikidata, Atlas Obscura, deep research
│   │   ├── utils/        # geo, HTTP client, sanitising, atomic file IO
│   │   └── middleware.py # rate limits, body cap, scanner block, security headers
│   └── tests/            # ~2,000 pytest tests (no network)
├── map/                  # Frontend: build-free PWA (app.html + css/ + js/ + sw.js)
├── deploy/               # nginx, systemd and backup examples
├── docs/                 # ARCHITECTURE.md, IDEAS.md
└── data/                 # GeoJSON & static geodata
```

Details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · frontend: [`map/README.md`](map/README.md) · contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- (Optional) an Anthropic or OpenAI API key for LLM features — core discovery works without one

### Install and run

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # optional: add API keys
uvicorn app.main:app --reload
```

Open <http://localhost:8000/> for the interactive map; the API docs are at `/docs`.

### Configuration

The main options are in [`backend/.env.example`](backend/.env.example); every setting lives in [`backend/app/config.py`](backend/app/config.py). The important ones:

| Variable | Description |
|----------|-------------|
| `TERRA_ANTHROPIC_API_KEY` / `TERRA_OPENAI_API_KEY` | LLM provider key (optional) |
| `TERRA_LLM_PROVIDER` | `anthropic` (default) or `openai` (`pip install ".[openai]"`) |
| `TERRA_LLM_DAILY_TOKEN_CAP` | Daily cap on LLM tokens (new calls stop once it is reached); `0` = unlimited |
| `TERRA_WIKIMEDIA_USER_AGENT` | Your app name and a contact URL/e-mail, sent to Wikimedia, Overpass and Nominatim (their policies ask for it) |
| `TERRA_ATLAS_OBSCURA_ENABLED` | `false` = never contact Atlas Obscura |
| `TERRA_CORS_ORIGINS` | Allowed origins, comma-separated (`*` for local use) |
| `TERRA_TRUSTED_PROXY_HOPS` | `1` behind nginx, so rate limits see real client IPs |
| `TERRA_GEMINI_API_KEY` | Google Gemini key for deep research (optional) |

### Tests and lint

```bash
cd backend
pytest tests -q
ruff check app tests
```

## 🐳 Docker

Build from the repository root so the map UI is included:

```bash
docker build -f backend/Dockerfile -t terra-incognita .
docker run -p 127.0.0.1:8000:8000 --env-file backend/.env \
  -v terra-data:/srv/terra/backend/data -v terra-cache:/srv/terra/backend/cache terra-incognita
```

`--env-file` needs the file to exist: `cp backend/.env.example backend/.env` (every line in it may stay commented out). Publishing on `127.0.0.1` keeps clients from reaching the app directly and forging `X-Forwarded-For` when nginx sits in front (drop the `127.0.0.1:` to expose it directly, and leave `TERRA_TRUSTED_PROXY_HOPS` at 0). The `data` volume holds your journal/XP/community data, the `cache` volume the cache and the LLM budget counter.

Run **one worker**: rate limits, the LLM budget and service state live in process memory. Production examples (nginx, systemd with journald, nightly backup) are in [`deploy/`](deploy).

## 📡 API Overview

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/discover` | POST | Find interesting places near coordinates (`sort_by`: `confidence`, `distance`, `name`, `unusual`) |
| `/api/place/enrich` | POST | Wikipedia summary + Commons photos for a place |
| `/api/daily` | GET | Place of the day |
| `/api/route`, `/api/route/explore`, `/api/route/loop` | POST | Point-to-point, exploration and loop routes |
| `/api/route/export` | POST | GPX / KML export |
| `/api/collections`, `/api/streak` | GET | Progress per category; day streak |
| `/api/chat`, `/api/describe`, `/api/recommend`, `/api/story` | POST | AI chat, descriptions, recommendations, stories |
| `/api/place/ask`, `/api/story/walk` | POST | Ask about one place; essay about a finished walk |
| `/api/visits`, `/api/trips`, `/api/stats`, `/api/stats/heatmap` | GET/POST… | Travel journal |
| `/api/fog/*`, `/api/achievements`, `/api/explorer/*` | GET/POST | Gamification |
| `/api/community/*` | GET/POST… | Community places, routes, reviews |
| `/api/offline/*` | GET/POST… | Offline regions and cache |
| `/health`, `/version` | GET | Liveness, LLM status and usage; version |

## 🌐 Data sources and their terms

Places come from OpenStreetMap (ODbL), Wikidata (CC0), Wikipedia and Wikimedia Commons (CC BY-SA / per-file licences) and, optionally, Atlas Obscura; the map uses third-party tile servers. Each comes with usage rules — attribution, fair use, a contact in the `User-Agent`. Read [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) before you run a public instance. Upgrading from an earlier version: [`docs/UPGRADING.md`](docs/UPGRADING.md).

## 🔒 Security notes

- Rate limits per IP (one table in `app/middleware.py`), a 256 KB body cap, bot-probe short-circuit, security headers and a strict CSP.
- LLM calls: user text is cleaned and fenced in prompts, outputs are validated, and `TERRA_LLM_DAILY_TOKEN_CAP` (off by default — set it) protects your bill: new calls stop at the cap, calls already running finish.
- **The app is single-explorer by design**: journal, XP and offline state belong to the instance, and community endpoints trust the client-supplied user id. Put it behind your own authentication before exposing it to people you don't trust. See *Known limits* in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 🛠️ Tech Stack

- **Backend**: Python 3.11+, FastAPI, Pydantic, httpx
- **Frontend**: Vanilla JS (no build step), MapLibre GL JS, service worker
- **Data**: OpenStreetMap Overpass, Wikidata SPARQL, Wikipedia & Wikimedia Commons, Atlas Obscura
- **AI/LLM**: Anthropic Claude (default) / OpenAI GPT (pluggable)
- **Storage**: JSON files with atomic writes; disk cache with TTL

## 🤝 Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). In short: fork, branch, `ruff check` + `pytest` clean, open a Pull Request. CI runs on Python 3.11–3.13.

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

# 🇷🇺 Русский

# 🌍 Terra Incognita

**Исследовательский помощник на базе ИИ** — открывайте скрытые, необычные и забытые места вокруг вас.

Terra Incognita объединяет открытые геоданные (OpenStreetMap, Wikidata, Wikipedia/Commons) и, по желанию, список Atlas Obscura с возможностями LLM, чтобы помочь путешественникам и городским исследователям находить места, которых нет в популярных путеводителях.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

---

## ✨ Возможности

- **🔍 Умный поиск** — заброшенные здания, военные объекты, подземные сооружения, руины и другие необычные места поблизости, отсортированные по объяснимой **оценке необычности** («заброшено · нет ссылки на Википедию · есть в Atlas Obscura»)
- **📖 Богатые карточки мест** — сводки из Википедии и фото из Wikimedia Commons с указанием авторов, подгружаются по запросу
- **🌅 Место дня** — одно необычное место рядом с вами и короткая история о нём
- **🧭 Маршруты** — из точки в точку, круговые прогулки и **петли на N часов** пешком или на велосипеде; экспорт в GPX/KML
- **🗺️ Интерактивная карта** — MapLibre GL с «туманом войны», тепловой картой ваших путешествий и светлой / тёмной / контрастной («солнечной») темами
- **📖 Дневник путешествий** — фиксация посещений с определением близости, поездки, заметки, экспорт
- **🏆 Геймификация** — очки опыта, достижения, открытие карты из-под тумана, **коллекции** («10/47 заброшек») и **серии** дней подряд
- **💬 ИИ-чат и «Спроси у места»** — поиск по настроению, вопросы о конкретном месте (голосовой ввод и озвучка — по желанию)
- **👥 Сообщество** — делитесь местами, маршрутами и отзывами
- **📡 Установка и офлайн** — PWA с офлайн-оболочкой и ограниченным кэшем тайлов
- **🛡️ Безопасность по умолчанию** — лимиты запросов по IP, строгий CSP, проверка входных данных и необязательный дневной лимит токенов LLM

## 🏗️ Архитектура

```
TerraIncognita/
├── backend/              # FastAPI-бэкенд (Discovery Engine)
│   ├── app/
│   │   ├── api/          # Тонкие REST-роутеры
│   │   ├── models/       # Модели Pydantic
│   │   ├── services/     # Конвейер поиска, оценка, маршруты, дневник, LLM-функции
│   │   ├── sources/      # OSM, Wikidata, Atlas Obscura, deep research
│   │   ├── utils/        # гео, HTTP-клиент, очистка ввода, атомарная запись файлов
│   │   └── middleware.py # лимиты, ограничение тела, блок сканеров, заголовки безопасности
│   └── tests/            # ~2000 тестов pytest (без сети)
├── map/                  # Фронтенд: PWA без сборки (app.html + css/ + js/ + sw.js)
├── deploy/               # примеры nginx, systemd и бэкапа
├── docs/                 # ARCHITECTURE.md, IDEAS.md
└── data/                 # GeoJSON и статические геоданные
```

Подробнее: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · фронтенд: [`map/README.md`](map/README.md) · участие: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## 🚀 Быстрый старт

### Требования

- Python 3.11+
- (Опционально) API-ключ Anthropic или OpenAI для функций ИИ — базовый поиск работает без него

### Установка и запуск

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # опционально: добавьте API-ключи
uvicorn app.main:app --reload
```

Откройте <http://localhost:8000/> — интерактивная карта; документация API — `/docs`.

### Настройка

Основные параметры — в [`backend/.env.example`](backend/.env.example), все настройки — в [`backend/app/config.py`](backend/app/config.py). Главные:

| Переменная | Описание |
|------------|----------|
| `TERRA_ANTHROPIC_API_KEY` / `TERRA_OPENAI_API_KEY` | Ключ LLM-провайдера (опционально) |
| `TERRA_LLM_PROVIDER` | `anthropic` (по умолчанию) или `openai` (`pip install ".[openai]"`) |
| `TERRA_LLM_DAILY_TOKEN_CAP` | Дневной лимит токенов LLM (новые вызовы останавливаются при его достижении); `0` — без лимита |
| `TERRA_WIKIMEDIA_USER_AGENT` | Название приложения и контакт (URL/e-mail): отправляется в Wikimedia, Overpass и Nominatim, их правила этого требуют |
| `TERRA_ATLAS_OBSCURA_ENABLED` | `false` — никогда не обращаться к Atlas Obscura |
| `TERRA_CORS_ORIGINS` | Разрешённые источники через запятую (`*` для локального запуска) |
| `TERRA_TRUSTED_PROXY_HOPS` | `1` за nginx, чтобы лимиты видели реальные IP клиентов |
| `TERRA_GEMINI_API_KEY` | Ключ Google Gemini для глубокого анализа (опционально) |

### Тесты и линтер

```bash
cd backend
pytest tests -q
ruff check app tests
```

## 🐳 Docker

Собирайте из корня репозитория, чтобы в образ попал интерфейс карты:

```bash
docker build -f backend/Dockerfile -t terra-incognita .
docker run -p 127.0.0.1:8000:8000 --env-file backend/.env \
  -v terra-data:/srv/terra/backend/data -v terra-cache:/srv/terra/backend/cache terra-incognita
```

`--env-file` требует существующий файл: `cp backend/.env.example backend/.env` (все строки в нём могут оставаться закомментированными). Публикация на `127.0.0.1` не даёт обращаться к приложению напрямую и подделывать `X-Forwarded-For`, когда впереди стоит nginx (уберите `127.0.0.1:`, чтобы открыть напрямую, и оставьте `TERRA_TRUSTED_PROXY_HOPS` равным 0). Том `data` хранит дневник, XP и данные сообщества, том `cache` — кэш и счётчик бюджета LLM.

Запускайте **один воркер**: лимиты, бюджет LLM и состояние сервисов хранятся в памяти процесса. Примеры для продакшена (nginx, systemd с journald, ночной бэкап) — в [`deploy/`](deploy).

## 📡 Обзор API

| Эндпоинт | Метод | Описание |
|----------|-------|----------|
| `/api/discover` | POST | Поиск интересных мест рядом (`sort_by`: `confidence`, `distance`, `name`, `unusual`) |
| `/api/place/enrich` | POST | Сводка из Википедии и фото из Commons для места |
| `/api/daily` | GET | Место дня |
| `/api/route`, `/api/route/explore`, `/api/route/loop` | POST | Маршруты: из точки в точку, исследовательский, петля |
| `/api/route/export` | POST | Экспорт в GPX / KML |
| `/api/collections`, `/api/streak` | GET | Прогресс по категориям; серия дней |
| `/api/chat`, `/api/describe`, `/api/recommend`, `/api/story` | POST | ИИ-чат, описания, рекомендации, истории |
| `/api/place/ask`, `/api/story/walk` | POST | Вопрос о месте; эссе о прогулке |
| `/api/visits`, `/api/trips`, `/api/stats`, `/api/stats/heatmap` | GET/POST… | Дневник путешествий |
| `/api/fog/*`, `/api/achievements`, `/api/explorer/*` | GET/POST | Геймификация |
| `/api/community/*` | GET/POST… | Места, маршруты и отзывы сообщества |
| `/api/offline/*` | GET/POST… | Офлайн-регионы и кэш |
| `/health`, `/version` | GET | Состояние, статус и расход LLM; версия |

## 🌐 Источники данных и их условия

Места берутся из OpenStreetMap (ODbL), Wikidata (CC0), Википедии и Wikimedia Commons (CC BY-SA / лицензии отдельных файлов) и, по желанию, из Atlas Obscura; карта использует сторонние серверы тайлов. У каждого свои правила: указание авторства, честное использование, контакт в `User-Agent`. Прочитайте [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) (на английском), прежде чем открывать публичный экземпляр. Обновляетесь с прошлой версии: [`docs/UPGRADING.md`](docs/UPGRADING.md).

## 🔒 Заметки о безопасности

- Лимиты по IP (одна таблица в `app/middleware.py`), ограничение тела запроса 256 КБ, отсечение ботов-сканеров, заголовки безопасности и строгий CSP.
- LLM: пользовательский текст очищается и «огораживается» в промптах, ответы проверяются, а `TERRA_LLM_DAILY_TOKEN_CAP` (по умолчанию выключен — задайте его) защищает ваш счёт: новые вызовы останавливаются на лимите, уже идущие завершаются.
- **Приложение задумано для одного исследователя**: дневник, XP и офлайн-состояние принадлежат экземпляру, а эндпоинты сообщества доверяют идентификатору пользователя от клиента. Прежде чем открывать его чужим людям, поставьте перед ним свою авторизацию. См. *Known limits* в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 🛠️ Технологический стек

- **Бэкенд**: Python 3.11+, FastAPI, Pydantic, httpx
- **Фронтенд**: Vanilla JS (без сборки), MapLibre GL JS, service worker
- **Данные**: OpenStreetMap Overpass, Wikidata SPARQL, Wikipedia и Wikimedia Commons, Atlas Obscura
- **ИИ/LLM**: Anthropic Claude (по умолчанию) / OpenAI GPT (подключаемые)
- **Хранение**: JSON-файлы с атомарной записью; дисковый кэш с TTL

## 🤝 Участие в разработке

См. [`CONTRIBUTING.md`](CONTRIBUTING.md). Коротко: форк, ветка, чистые `ruff check` и `pytest`, Pull Request. CI гоняет тесты на Python 3.11–3.13.

## 📄 Лицензия

Проект распространяется под лицензией MIT — подробности в файле [LICENSE](LICENSE).
