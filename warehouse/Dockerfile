# syntax=docker/dockerfile:1
#
# dbt against a local DuckDB file -- lets developers run `dbt build`
# without BigQuery access (see docker-compose.yml's `dbt` service, and
# warehouse/profiles.yml.example's `dev` target).
#
# Build context is warehouse/. Build with:  docker build -f warehouse/Dockerfile warehouse/

FROM python:3.12-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY . .
ENV PATH="/opt/venv/bin:$PATH"

ENTRYPOINT ["dbt"]
CMD ["build", "--profiles-dir", "."]
