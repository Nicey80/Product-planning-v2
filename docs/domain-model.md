# Domain model

This document is the formal specification of the subscription base and
movements forecasting domain. `CLAUDE.md` holds the condensed glossary and
invariant list for quick reference; this document explains the *why* and
the mechanics behind each piece, and is the reference for anyone
implementing or reviewing `engine/`. Terminology is identical to
`CLAUDE.md` — read that first if you haven't.

## 1. Dimensions

### 1.1 Product hierarchy — Node

```
product_group
  └── product
        └── variant   (= Node, always a leaf)
```

All forecasting and reconciliation is stated "per node," i.e. per leaf
`variant`. `product_group` and `product` are roll-up levels only — they
never carry their own independent forecast; they are always the sum of
their child nodes (invariant 6).

### 1.2 Channel hierarchy — Channel

```
channel_group
  └── channel
        └── sub_channel   (leaf; "partner" in partner-led channels)
```

Same roll-up rule: `channel_group` and `channel` totals are always sums of
their leaf `sub_channel` values.

### 1.3 order_channel vs acquisition_channel

Every **order** carries an `order_channel` — the channel that raised that
specific order. This is purely transactional: it describes where the
paperwork happened.

Every **base record** (a subscriber's current position on a node) carries
an `acquisition_channel` — the channel that raised the acquisition order
that first brought that subscriber into the base. It is:

- Set exactly once, at the moment the acquisition order **closes**.
- Immutable and inherited across every subsequent event for that
  subscriber's subscription lineage, including regrades (a regrade's
  `resign_to` base record keeps the *original* `acquisition_channel`, not
  the `order_channel` of the regrade order) — a regrade is not a
  reacquisition.
- Cleared only when the subscriber churns and the base record is removed.
- Only ever assigned by migration_acq for portfolio-internal moves that
  create a base record with no originating order (see §5); the value used
  there is whatever the migration overlay specifies as the inherited
  acquisition channel from the source system/book.

This split exists because reporting must be able to answer both "which
channel raised this quarter's orders" (`order_channel`, transaction-level)
and "which channel is this cohort of the base originally attributable
to" (`acquisition_channel`, subscriber-level, sticky).

## 2. Order lifecycle

An order is created **raised** and moves to exactly one terminal state:

```
            ┌──────────┐   g(k) at lag k   ┌────────┐
  raise ──▶ │  (open)  │ ────────────────▶ │ closed │
            │  ageing  │                   └────────┘
            └────┬─────┘
                 │ breakage
                 ▼
             ┌────────┐
             │ broken │
             └────────┘
```

- Raised, closed and broken are each recorded as a dated event
  (`period`, `node`, `order_channel`, `txn_type`, `count`/`value`).
- `txn_type ∈ {acquisition, regrade, churn}`. Migration moves are **not**
  a txn_type on the order pipeline — they never raise an order (§5).
- An order that is still open (raised, not yet closed or broken) at the
  end of a period contributes to `open_orders[t]` (Identity 1) and is
  aged forward into `t+1` unless it resolves.
- The **closure kernel** `g(k)` (§3) governs, for orders raised in period
  `s`, what fraction close in `s+k` for each future `k ≥ 0`. The
  remainder — `1 - Σ_k g(k)` — is `breakage`, orders from that raise
  cohort that never close.
- A raised order is forecast/tracked by its **raise cohort and age**
  (`k` = periods since raise), not just its current period, because the
  kernel must be applied per-cohort: two orders open in the same period
  but raised in different periods have different close probabilities for
  the *next* period.

### 2.1 Acquisitions and regrades are forecast at the raised stage

The engine's forecasting target is **raise volume**, not close volume.
Forecast `raised[t]` per node × order_channel × txn_type, then apply the
closure kernel forward to derive expected `closed[t']` and `broken[t']`
for future periods. This is deliberate: raise volume is the controllable/
plannable quantity (marketing spend, sales activity); close volume is a
downstream consequence of raise volume and cycle-time/breakage dynamics
that shift independently (e.g. ops backlog lengthening cycle time without
any change in demand).

## 3. Closure kernel

For a given segment (typically node × order_channel × txn_type, though
coarser segments may be configured where data is sparse):

```
g(k) = P(order raised in period s closes in period s + k),   k = 0, 1, 2, ...
breakage = 1 - Σ_k g(k)
```

Contract (invariant 2): `Σ_k g(k) + breakage == 1` exactly, per segment.
`g` is typically estimated empirically from historical raise→close lags
and refreshed per forecast run; it is a **run input**, not a hardcoded
constant, so different runs may carry different kernels (e.g. scenario:
"ops backlog clears in Q3" = a kernel with shorter lag from that period
forward).

Applying the kernel to a raise forecast produces expected closes:

```
closed[t] = Σ_s raised[s] * g(t - s)     for all s ≤ t
broken[t] = Σ_s raised[s] * breakage_s   (breakage realized at/после resolution, per model config)
```

The exact period at which breakage is *recognized* (immediately vs. at
the tail of the kernel) is a modeling choice made explicit in run
parameters, but wherever it lands, invariant 4
(`closed + broken + open ≤ raised`, cumulative) must hold at every period,
meaning breakage can never be recognized before or in excess of its raise
cohort.

## 4. Order book

The order book is the stock of orders that have been raised but have
neither closed nor broken yet. It is carried forward period over period
per Identity 1:

```
open_orders[t] = open_orders[t-1] + raised[t] - closed[t] - broken[t]
```

Because the kernel is age-dependent, the engine tracks the order book
**by raise cohort and age**, not just as a single scalar per node ×
order_channel × txn_type — the scalar `open_orders[t]` is the sum across
all still-open cohorts, and is what Identity 1 and invariant 5
(`open_orders ≥ 0`) are checked against, but the internal representation
needed to apply `g(k)` correctly is cohort-aged.

## 5. Base and its movements

The base is the count of active subscribers per node. It only moves on
**closed** transactions and **migration overlays** — never on raises
(invariant 7), and never directly on breaks (a break simply means the
order never affects the base at all).

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

- `closed_acquisition[t]` — closed acquisition orders add to the node's
  base and stamp `acquisition_channel` for the new base records.
- `closed_resign_to[t]` / `closed_resign_from[t]` — see §5.1.
- `churn[t]` — closed churn orders remove subscribers from the base at
  their node. Only closed churn counts; a raised-but-not-yet-closed churn
  order is still in the order book and has not left the base.
- `migration_acq[t]` / `migration_churn[t]` — see §5.2.

### 5.1 Regrade mechanics

A regrade is a single business event (subscriber moves from node A to
node B, and/or extends their contract) but it is modeled as a **linked
pair** of base movements once its order closes:

- `closed_resign_from[t]` at node A (the source) — the subscriber leaves
  A's base.
- `closed_resign_to[t]` at node B (the destination) — the subscriber
  enters B's base, carrying forward their original `acquisition_channel`.

Both legs are created together, in the same period, from the same closed
order — never independently. This is what makes invariant 3 hold:
`Σ closed_resign_to == Σ closed_resign_from` across all nodes in a period,
because every `resign_to` row has exactly one `resign_from` row as its
pair (and vice versa) — regrades move subscribers within the total base,
they never create or destroy a subscriber on their own the way an
acquisition or churn does.

A regrade order that only extends a contract without changing node (a
pure contract extension) still produces a `resign_from`/`resign_to` pair
on the *same* node — it is a no-op on the base identity's node-level
values (they cancel) but is still recorded as a closed regrade event for
lineage/reporting purposes (e.g. contract-extension counts).

### 5.2 Migration overlays

Migrations are portfolio-internal forced moves — data corrections, system
migrations, book transfers between nodes — that must adjust the base
without ever having gone through an order:

- They are entered directly as `migration_acq` / `migration_churn`
  overlay rows against the base, dated to the period they take effect.
- They are always created as a **linked pair**: a `migration_churn` at
  the source node and a `migration_acq` at the destination node (or, for
  a pure correction with no destination, a pair that still nets to zero
  across whatever scope the correction is defined over — migrations are
  never a one-sided adjustment).
- They **never** create a `raised`, `closed`, or `broken` row, never carry
  an `order_channel`, and are never subject to the closure kernel
  (invariant 8). They also do not appear in Identity 1 at all — only in
  Identity 2.
- Because they're paired and net to zero, they cannot be used to
  manufacture or destroy subscribers — only to relocate them across nodes
  outside the normal acquisition/regrade/churn pipeline.

## 6. Hierarchy and cross-margin reconciliation

Invariant 6 requires that leaf-node forecasts sum exactly to every parent
level on **both** hierarchies independently, and that the node × channel
cross-tabulation is consistent no matter which axis you sum first:

```
Σ_variant forecast(variant, ...)         == forecast(product, ...)
Σ_product forecast(product, ...)         == forecast(product_group, ...)
Σ_sub_channel forecast(..., sub_channel) == forecast(..., channel)
Σ_channel forecast(..., channel)         == forecast(..., channel_group)

Σ_node Σ_channel cell[node, channel] == Σ_channel Σ_node cell[node, channel]
                                      == grand_total
```

This is not just a rounding-tolerance check — the engine should compute
parent-level and cross-margin values by **summation of the same
underlying leaf cells**, not by separately forecasting at multiple grains,
so the identity holds exactly rather than approximately. Where a forecast
model operates at a coarser grain than the leaf (e.g. sparse-data
fallback to `channel` instead of `sub_channel`), it must allocate back
down to leaves deterministically (e.g. by a fixed or historical split)
before being summed back up, so the exact-sum contract at every level is
preserved.

## 7. Forecast runs

A forecast run is the unit of reproducibility: a single execution of the
engine over a named scenario/assumption set (raise forecasts, kernels,
breakage assumptions, migration overlay schedule, run parameters such as
random seed) producing a full set of period-by-node-by-channel outputs
satisfying every invariant in `CLAUDE.md`.

- **Immutable once created** (invariant 9): once a run's inputs are
  captured and its outputs computed, neither may be edited in place.
  Persisted run records are write-once.
- A correction, a re-forecast, or a new scenario is always a **new run**
  with a new identifier, even if it's "the same as run N but with an
  updated kernel." This makes every historical run auditable and
  comparable, and makes the engine's purity requirement (see
  `CLAUDE.md` § Conventions) enforceable: given the same inputs and seed,
  a run's outputs are byte-for-byte reproducible.
- Downstream consumers (backend API, warehouse marts) reference outputs
  by `(run_id, period, node, order_channel/channel, ...)` and must never
  write back into a run's own tables.

## 8. What `engine/` computes vs. what it doesn't

`engine/` owns: raise forecasting, kernel application (raise → close/
break), order book roll-forward (Identity 1), base roll-forward
(Identity 2), regrade pairing, migration overlay application, and
hierarchy roll-up/reconciliation (Identity 6). It does not own
persistence, HTTP, auth, or UI — those are `backend/` and `frontend/`
concerns that consume `engine/`'s pure functions/data structures.

`warehouse/` restates the same two identities in SQL, but over **actuals**
(historical closed transactions and recorded base movements), for BI and
for validating forecast accuracy against what actually happened — it is
not a second implementation of the forecasting logic itself.
