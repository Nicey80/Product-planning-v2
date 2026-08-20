# syntax=docker/dockerfile:1
#
# Build context is the repo root (backend/ depends on engine/ as a
# sibling path dependency -- see backend/pyproject.toml [tool.uv.sources]).
# Build with:  docker build -f backend/Dockerfile .
#
# Serves both Cloud Run deployables built from this image:
#   - the API service (default CMD, uvicorn on $PORT)
#   - the forecast Cloud Run Job (command/args overridden in
#     infra/modules/app/cloud_run_jobs.tf to run backend.jobs.run_forecast)

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY engine/ ./engine/
COPY backend/pyproject.toml backend/uv.lock ./backend/
WORKDIR /app/backend
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM base AS runtime
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app/backend
COPY --from=builder /app/engine /app/engine
COPY --from=builder /app/backend /app/backend
ENV PATH="/app/backend/.venv/bin:$PATH" \
    PORT=8080
USER appuser
EXPOSE 8080
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
