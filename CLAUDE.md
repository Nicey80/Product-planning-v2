# CLAUDE.md

This file is the entry point for any engineer or agent working in this
repository. It defines the vocabulary, the mathematical contracts, the
stack, and the conventions that all later work must follow. If code and
this document disagree, the document wins until it is deliberately
updated — do not "fix" the document to match code that violates an
invariant.

## What this is

A subscription base and movements forecasting application. It forecasts
subscription **acquisitions**, **regrades**, and **churn** through an
order pipeline (raise → close/break), rolls closed transactions into a
subscriber **base**, and reconciles everything against exact accounting
identities across a product hierarchy and a channel hierarchy.

## Repository layout

```
CLAUDE.md               this file
docs/domain-model.md     narrative + formal domain model
backend/                 FastAPI service (Python 3.12, uv)
frontend/                React + TypeScript app (Vite)
engine/                  standalone forecasting library (importable, no web deps)
warehouse/               dbt project (staging -> marts over base/order-book facts)
.github/workflows/       CI
```

`engine/` is the source of truth for forecasting logic. `backend/` depends
on `engine/` as a library dependency; it never reimplements forecasting
math. `frontend/` talks to `backend/` over HTTP only. `warehouse/` models
the same identities in SQL for BI/reporting over historical actuals — it
does not forecast.

## Glossary

Use these terms exactly, everywhere — code, comments, docs, API fields,
dbt model names, UI copy. Do not introduce synonyms (no "product line" for
"product group", no "acquisition source" for "acquisition_channel").

- **Node** — leaf product sub-variant. Product hierarchy: `product_group →
  product → variant`. A node is always a leaf `variant`; forecasts and
  identities are stated "per node" meaning per leaf variant, rolled up
  through the hierarchy.
- **Channel** — selling channel. Channel hierarchy: `channel_group →
  channel → sub_channel` (sub-channel/partner is the leaf).
- **order_channel** — the channel that raised a given order. An attribute
  of the *transaction* (order). Can differ from `acquisition_channel` for
  a subscriber whose base-defining order was raised elsewhere at a later
  point (e.g. a regrade raised through a different channel than the
  original acquisition).
- **acquisition_channel** — the channel that originally acquired a
  subscription. A *sticky* attribute of the subscriber: set once, at the
  closure of the acquisition order, and inherited by every subsequent base
  record for that subscriber (including through regrades) until churn.
- **Raised** — an order has been placed. This is the pipeline entry point.
  Acquisitions and regrades are **forecast at the raised stage** — the
  forecasting engine predicts raise volumes and then propagates them
  through the closure kernel, not the other way around.
- **Closed** — an order has completed (fulfilled). Only closed transactions
  affect the base. "Closed" is a terminal, immutable state for an order.
- **Broken** — a raised order that is cancelled, fails, or is rejected and
  never closes. Terminal, immutable state, mutually exclusive with closed.
- **Breakage rate** — the proportion of raised orders that ultimately
  break: `breakage = 1 - Σ_k g(k)` for a given kernel segment.
- **Cycle time** — the lag, in periods, between raise and close. It is a
  **distribution**, not a point estimate — different orders raised in the
  same period close in different future periods.
- **Closure kernel** — `g(k)` = probability an order raised in period `s`
  closes in period `s + k`. Defined per segment (node × order_channel ×
  txn_type, or as configured). Contract: `Σ_k g(k) + breakage == 1`.
- **Order book** — the stock of raised-but-not-yet-resolved orders
  (neither closed nor broken), carried across periods and tracked by age
  (periods since raise) so the closure kernel can be applied age-cohort by
  age-cohort.
- **Acquisition** — a new subscription sale (no prior subscription for
  that subscriber on this product).
- **Regrade** — conversion of an existing subscription to a different
  sub-product and/or a contract extension. On close, a regrade produces a
  linked pair: `resign_from` at the source node and `resign_to` at the
  destination node, in the same period.
- **Churn** — end of a subscription. Only recognized on closed churn
  transactions.
- **Base** — subscriber volume at a point in time, per node.
- **Migration acq / migration churn** — portfolio-internal forced moves
  (e.g. system migrations, book transfers). Overlay-driven (entered
  directly against the base, not forecast from a kernel), always created
  as **linked pairs** that net to zero across the affected nodes, and they
  **bypass the order pipeline entirely** — no raise, no order_channel, no
  kernel.
- **Forecast run** — a single, versioned, timestamped execution of the
  engine over a scenario/assumption set. **Immutable once created** — a
  correction is a new run, never a mutation of an old one.

## Identities

These are exact accounting identities. They must hold at every node,
every channel, every period — not just in aggregate.

**Identity 1 — order book** (per node × order_channel × txn_type):

```
open_orders[t] = open_orders[t-1] + raised[t] - closed[t] - broken[t]
```

**Identity 2 — base** (per node, closed transactions only):

```
closing_base[t] = opening_base[t]
                 + closed_acquisition[t]
                 + closed_resign_to[t]
                 - closed_resign_from[t]
                 - churn[t]
                 + migration_acq[t]
                 - migration_churn[t]

opening_base[t] = closing_base[t-1]
```

Raised volumes never appear in Identity 2. The base only ever moves on
closed transactions and migration overlays (invariant 7).

## Invariants

These are the contract for everything downstream. They are written as
Hypothesis property-based tests in `engine/tests/test_invariants.py`
**before** any forecasting implementation exists, and every later change
to `engine/` must keep them green.

1. Both identities hold exactly, at every node, channel and period.
2. `Σ_k g(k) + breakage == 1` for every kernel segment.
3. `Σ closed_resign_to == Σ closed_resign_from` across nodes, in each
   period (regrades net to zero across the product hierarchy).
4. `cumulative_closed + cumulative_broken + open ≤ cumulative_raised`, per
   node × channel (nothing resolves more orders than were ever raised).
5. `open_orders ≥ 0` and `base ≥ 0` at every node and period.
6. Leaf forecasts sum exactly to parents on both the product hierarchy and
   the channel hierarchy, and to their cross-margins (node × channel
   totals reconcile both ways).
7. Raised volumes never directly affect the base — only closures
   (`closed_*`) and migration overlays do.
8. Migration overlays are paired, net to zero, and never pass through the
   order pipeline (no raised/closed/broken rows are created for them).
9. Forecast runs are immutable once created — no in-place mutation of a
   run's inputs or outputs after creation; corrections are new runs.

Do not weaken, skip, or `xfail` these tests to make a feature ship. If an
invariant appears to conflict with a new requirement, stop and resolve the
conflict in `docs/domain-model.md` first.

## Stack

- **backend/** — Python 3.12, [uv](https://docs.astral.sh/uv/) for env +
  dependency management, FastAPI, Pydantic v2. Depends on `engine/` as a
  local/path dependency. Tests with `pytest`.
- **frontend/** — React + TypeScript, Vite. ESLint + Prettier. Tests with
  `vitest` + Testing Library.
- **engine/** — Python 3.12, uv, zero web framework dependencies — must
  remain importable standalone (e.g. from a notebook, a batch job, or
  `warehouse/` tooling) with no FastAPI/React coupling. `mypy --strict` is
  mandatory on this package. Property-based tests with `hypothesis`,
  example/unit tests with `pytest`.
- **warehouse/** — dbt. Staging models land raw closed-order and base-
  movement facts; marts materialize Identity 1 and Identity 2 as
  reconciliation models over actuals.
- **Tooling** — `pre-commit` running `ruff` (lint + format) on all Python,
  `mypy --strict` scoped to `engine/`, `eslint` + `prettier` on
  `frontend/`. GitHub Actions CI runs the full matrix (pytest × 2, vitest,
  mypy, ruff, eslint, dbt parse) on every PR.

## Conventions

- **Terminology discipline.** Use glossary terms verbatim in code
  identifiers, API payloads, DB columns, and dbt model/column names:
  `node`, `order_channel`, `acquisition_channel`, `raised`, `closed`,
  `broken`, `breakage_rate`, `cycle_time`, `closure_kernel`,
  `open_orders`, `acquisition`, `regrade`, `resign_from`, `resign_to`,
  `churn`, `base`, `migration_acq`, `migration_churn`, `forecast_run`.
- **Money/volume types.** Use `Decimal` (Python) for anything that
  accumulates across periods (base, order book counts if fractional
  probabilities are involved) to avoid float drift breaking the exact
  identities. Kernel probabilities may be `float` but must be validated to
  sum to 1 within a tight tolerance, not accumulated without control.
- **Time.** Periods are discrete and ordered (e.g. ISO week or month keys)
  — never wall-clock timestamps — for all forecasting math. Store the
  period grain explicitly; don't infer it from string parsing.
- **Engine purity.** `engine/` functions that compute forecasts must be
  pure and deterministic given (inputs, run parameters, random seed). No
  hidden I/O, no wall-clock reads, no global mutable state — this is what
  makes invariant 9 (run immutability) enforceable and what makes
  property-based testing tractable.
- **Tests first for contracts.** Any change to the identities, the
  kernel, or the hierarchy roll-up logic starts with a
  failing/updated property test in `engine/tests/test_invariants.py`
  before implementation changes.
- **Commits.** Small, imperative-mood commit messages describing why, not
  a changelog of what. Do not commit generated artifacts (dbt `target/`,
  `node_modules/`, `.venv/`, build output).
- **No premature abstraction.** Match the existing house style: don't
  build config-driven generality for a single call site; don't add
  fallbacks for states the identities prove can't happen.
