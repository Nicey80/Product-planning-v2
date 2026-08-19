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
| `warehouse/`| dbt project restating the base/order-book identities over actuals |

## Getting started

```sh
# engine
cd engine && uv sync && uv run pytest

# backend (depends on engine/ via a local uv path dependency)
cd backend && uv sync && uv run pytest

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
