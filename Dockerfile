# Kostolany Watch API — Cloud Run
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install ".[korea]"

EXPOSE 8080

# Cloud Run injects PORT
CMD exec uvicorn kostolany.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
