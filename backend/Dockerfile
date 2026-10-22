# Build from the repository root so the map UI and static data ship with the API:
#   docker build -f backend/Dockerfile -t terra-incognita .
#   docker run -p 8000:8000 --env-file backend/.env -v terra-data:/srv/terra/backend/data terra-incognita
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# app/main.py resolves ../../map and ../../data relative to itself, so keep the repo layout.
WORKDIR /srv/terra/backend

COPY backend/pyproject.toml backend/README.md ./
COPY backend/app/ app/
RUN pip install --no-cache-dir ".[openai]"

COPY map/ /srv/terra/map/
COPY data/ /srv/terra/data/

RUN useradd --system --create-home terra \
    && mkdir -p /srv/terra/backend/data /srv/terra/backend/cache \
    && chown -R terra /srv/terra
USER terra

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/version')" || exit 1

# One worker: rate limits and LLM budget live in process memory (see docs/ARCHITECTURE.md).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
