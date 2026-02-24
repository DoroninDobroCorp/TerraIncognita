FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY app/ app/

EXPOSE 8000

# Note: To serve map UI, build from project root:
#   docker build -f backend/Dockerfile -t terra-incognita .
# and add COPY map/ /app/../map/ and COPY data/ /app/../data/
# Or use docker-compose with volume mounts.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
