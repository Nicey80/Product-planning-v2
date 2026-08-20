# Product-planning-v2

Subscription base and movements forecasting application.

Start here:

- **[CLAUDE.md](CLAUDE.md)** — glossary, invariants, stack, and
  conventions. Read this before touching any code.
- **[docs/domain-model.md](docs/domain-model.md)** — the full domain
  specification the codebase implements.

## Layout

| Path        | What it is                                                        |
| ----------- | ------------------------------------------------------------------ |
| `engine/`   | Standalone forecasting library (Python 3.12, uv, `mypy --strict`) |
| `backend/`  | FastAPI service, depends on `engine/` (Python 3.12, uv)           |
| `frontend/` | React + TypeScript app (Vite)                                     |
| `warehouse/`| dbt project (BigQuery in prod, DuckDB for local dev) restating the base/order-book identities over actuals |
| `infra/`    | Terraform: Cloud Run, Cloud SQL, BigQuery, Artifact Registry, WIF — see [infra/README.md](infra/README.md) |

## Getting started

```sh
# engine
cd engine && uv sync && uv run pytest

# backend (depends on engine/ via a local uv path dependency)
cd backend && uv sync && uv run pytest

# backend persistence layer (Postgres via SQLAlchemy 2.0 + Alembic)
createdb subscription_forecasting
export DATABASE_URL=postgresql+psycopg:///subscription_forecasting
uv run alembic upgrade head

# frontend
cd frontend && npm install && npm test

# warehouse (dbt against a local DuckDB file)
cd warehouse && uv sync
cp profiles.yml.example profiles.yml
uv run dbt build --profiles-dir .
```

Install [pre-commit](https://pre-commit.com/) hooks once, at the repo
root:

```sh
pre-commit install
```

## Local development with Docker

`docker-compose.yml` runs Postgres, the API (live-reloading), and the
frontend dev server, plus an on-demand dbt-against-DuckDB service so
nobody needs BigQuery access to run `dbt build` locally:

```sh
docker compose up            # postgres, api (http://localhost:8000), frontend (http://localhost:5173)

cp warehouse/profiles.yml.example warehouse/profiles.yml   # once
docker compose run --rm dbt build
docker compose run --rm dbt test
```

The API applies pending Alembic migrations on startup in this stack.

## CI/CD

GitHub Actions (`.github/workflows/`):

- **`ci.yml`** — the validation pipeline: lint → typecheck → unit →
  property tests → golden-run snapshot → dbt tests → build. Runs as a
  reusable workflow, called from `deploy.yml`.
- **`deploy.yml`** — entry point on every push/PR. Runs `ci.yml`, then:
  - **pull request** → an ephemeral, 0%-traffic Cloud Run revision
    tagged `pr-<number>`, with its preview URL posted as a PR comment;
  - **push to `main`** → deploy staging;
  - **push of a `v*` tag** → deploy prod, gated on manual approval via
    the `production` GitHub Environment's required reviewers.
- **`deploy-env.yml`** — the reusable per-environment deploy job (image
  build/push, db migration, `gcloud run deploy`), called three times by
  `deploy.yml`. Authenticates to GCP via Workload Identity Federation —
  no service account keys anywhere.

See [infra/README.md](infra/README.md) for the Terraform that provisions
what these workflows deploy to, and the one-time GitHub Environment
variables each needs.

## Observability

The API and the forecast job emit structured JSON logs (see
`backend/src/backend/observability/logging.py`) with a `run_id` bound to
every log line for the duration of a forecast run
(`backend.observability.run_id_scope`), and export OpenTelemetry traces
(`OTEL_EXPORTER_OTLP_ENDPOINT`). The API exposes `/healthz` (liveness)
and `/readyz` (readiness — checks the database connection).

Every `forecast_run` row records its engine version, kernel version,
book snapshot date, a config hash, input data versions, and the git SHA
it ran at (`backend/src/backend/db/models/forecast.py`), so any
historical run can be reproduced exactly (CLAUDE.md invariant 9).
