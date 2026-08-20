"""Golden fixture harness: a frozen synthetic dataset at a pinned seed, run
through engine's full estimate -> forecast pipeline, diffed byte-for-byte
against committed snapshots on every CI run (test_golden_synthetic.py).

Why synthetic, not real data
------------------------------
A golden fixture needs a realistic, nontrivial dataset committed to the
repo -- but real historical order/subscriber data can never be committed
(customer PII, business-sensitive volumes). engine/testing/synthetic.py's
whole purpose is to stand in for exactly this: a portfolio generated from
known parameters at a pinned seed is fully synthetic by construction, so
freezing it as a fixture carries no privacy risk while still exercising
the full pipeline against realistic-shaped data (multiple nodes, channels,
contract terms, tenure-dependent churn, right-censored recent cohorts).

Two committed snapshots, two different things they guard
-----------------------------------------------------------
- golden/synthetic_dataset_v1.json is generate_portfolio(PINNED_PARAMS)'s
  raw output. If this stops matching, generate_portfolio's own determinism
  broke (same seed, different data) -- a change in engine/testing/synthetic.py.
- golden/golden_forecast_run_v1.json is what run_forecast() computes FROM
  that frozen dataset, using engine/estimate.py, kernel.py, simulate.py,
  and hierarchy.py. If this stops matching but the dataset snapshot still
  matches, some downstream engine computation changed its numeric
  behavior -- not the data generator.

Scope of the forecast in this fixture
----------------------------------------
run_forecast() estimates every parameter type engine/estimate.py supports
(closure kernel + breakage per acquisition segment, the regrade transition
matrix, churn hazard by tenure and contract term) and snapshots all of
them -- but only *projects forward* the acquisition -> closure -> churn
chain (via apply_closure_kernel and simulate_node_base) into a hierarchy
rollup. Regrade is estimated and reported as a parameter but deliberately
not fed into the projected base, to keep the fixture legible: exercising
every possible combination of engine primitives is not this fixture's
job, only proving the pipeline chains together deterministically end to
end.

Regenerating goldens
---------------------
When a change to engine/ deliberately changes numeric behavior (a new
estimator, a bugfix to the closure kernel, a change to how breakage is
recognized, etc.), both snapshots need a reviewed, deliberate update --
never a silent side effect of an unrelated change. From engine/:

    uv run python tests/golden/regenerate_synthetic_golden.py

Then inspect `git diff tests/golden/` and confirm every changed number is
explained by the change you made, before committing the regenerated
snapshots as their own reviewed commit -- not bundled with the code change
that caused them to move.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from engine.base import BaseMovements, roll_forward_base
from engine.domain import NodeId, Period, TxnType
from engine.estimate import (
    estimate_churn_hazard,
    estimate_closure_kernel,
    estimate_regrade_transition,
    observations_from_order_events,
    raised_by_cohort_from_events,
)
from engine.hierarchy import roll_up
from engine.kernel import apply_closure_kernel
from engine.testing.synthetic import (
    ClosureProfile,
    SyntheticParams,
    SyntheticPortfolio,
    generate_portfolio,
    make_channel_hierarchy,
    make_product_hierarchy,
    observation_cutoff,
)

_ZERO = Decimal(0)

# ---------------------------------------------------------------------------
# Pinned ground-truth parameters. Never change these in place -- a change
# here is a change to the dataset itself and needs the regeneration
# procedure above, same as any other intentional golden update.
# ---------------------------------------------------------------------------

_PRODUCT_HIERARCHY = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=2)
_CHANNEL_HIERARCHY = make_channel_hierarchy(
    groups=1, channels_per_group=1, sub_channels_per_channel=2
)
_NODES = _PRODUCT_HIERARCHY.nodes
_CHANNELS = _CHANNEL_HIERARCHY.sub_channels

_ACQUISITION_PROFILE = ClosureProfile(
    g=(Decimal("0.5"), Decimal("0.3"), Decimal("0.1")), breakage=Decimal("0.1")
)
_REGRADE_PROFILE = ClosureProfile(g=(Decimal("0.6"), Decimal("0.2")), breakage=Decimal("0.2"))
_CHURN_PROFILE = ClosureProfile(g=(Decimal("0.8"),), breakage=Decimal("0.2"))

_TRUE_CHURN_HAZARD = {"monthly": Decimal("0.05"), "annual": Decimal("0.02")}


def _churn_hazard(_tenure: int, term: str) -> Decimal:
    return _TRUE_CHURN_HAZARD[term]


PINNED_PARAMS = SyntheticParams(
    product_hierarchy=_PRODUCT_HIERARCHY,
    channel_hierarchy=_CHANNEL_HIERARCHY,
    horizon=24,
    seed=20260101,
    acquisition_rate={(n, c): Decimal("6") for n in _NODES for c in _CHANNELS},
    contract_terms=("monthly", "annual"),
    contract_term_weights=(Decimal("0.7"), Decimal("0.3")),
    regrade_transition={n: {m: Decimal(1) / len(_NODES) for m in _NODES} for n in _NODES},
    regrade_hazard=lambda _t, _c: Decimal("0.02"),
    churn_hazard=_churn_hazard,
    closure={
        **{(c, TxnType.ACQUISITION): _ACQUISITION_PROFILE for c in _CHANNELS},
        **{(c, TxnType.REGRADE): _REGRADE_PROFILE for c in _CHANNELS},
        **{(c, TxnType.CHURN): _CHURN_PROFILE for c in _CHANNELS},
    },
    order_channel_weight={c: Decimal(1) for c in _CHANNELS},
)

_FORECAST_HORIZON = 6  # periods projected forward beyond the dataset
_KERNEL_MAX_AGE = 6
_CHURN_MAX_TENURE = 10
_REPRESENTATIVE_TENURE = 5  # tenure bucket used for the projected churn rate
_NAIVE_FORECAST_WINDOW = 6  # trailing periods averaged for the raise forecast


def build_dataset() -> SyntheticPortfolio:
    return generate_portfolio(PINNED_PARAMS)


def run_forecast(portfolio: SyntheticPortfolio) -> dict[str, Any]:
    """Estimate every parameter type from `portfolio`, then project the
    acquisition -> closure -> churn chain forward and roll it up to the
    product hierarchy. See the module docstring for scope."""
    cutoff = observation_cutoff(portfolio)

    estimated_kernels: dict[str, Any] = {}
    projected_closed_by_node: dict[NodeId, dict[Period, Decimal]] = {n: {} for n in _NODES}

    for node in _NODES:
        for channel in _CHANNELS:
            observations = observations_from_order_events(
                portfolio.raw_order_event,
                node=node,
                order_channel=channel,
                txn_type=TxnType.ACQUISITION,
            )
            raised = raised_by_cohort_from_events(
                portfolio.raw_order_event,
                node=node,
                order_channel=channel,
                txn_type=TxnType.ACQUISITION,
            )
            kernel = estimate_closure_kernel(
                observations,
                raised,
                node=node,
                order_channel=channel,
                txn_type=TxnType.ACQUISITION,
                observation_cutoff=cutoff,
                max_age=_KERNEL_MAX_AGE,
            )
            estimated_kernels[f"{node}|{channel}"] = {
                "g": [str(v) for v in kernel.g],
                "breakage": str(kernel.breakage),
            }

            trailing = [
                raised.get(Period(int(cutoff) - k), _ZERO) for k in range(_NAIVE_FORECAST_WINDOW)
            ]
            forecast_rate = sum(trailing, start=_ZERO) / _NAIVE_FORECAST_WINDOW
            forecast_raised = {
                Period(int(cutoff) + 1 + k): forecast_rate for k in range(_FORECAST_HORIZON)
            }
            closure_result = apply_closure_kernel(forecast_raised, kernel)
            for period, value in closure_result.closed.items():
                projected_closed_by_node[node][period] = (
                    projected_closed_by_node[node].get(period, _ZERO) + value
                )

    estimated_regrade_transition = {
        str(source): {str(dest): str(p) for dest, p in dist.items()}
        for source, dist in estimate_regrade_transition(portfolio.raw_subscription_event).items()
    }

    estimated_churn_hazard = estimate_churn_hazard(
        portfolio.raw_subscription_event, observation_cutoff=cutoff, max_tenure=_CHURN_MAX_TENURE
    )
    blended_churn_rate: dict[str, Decimal] = {
        term: estimated_churn_hazard.get((_REPRESENTATIVE_TENURE, term), _ZERO)
        for term in PINNED_PARAMS.contract_terms
    }
    projected_churn_rate = sum(
        (
            blended_churn_rate[t] * w
            for t, w in zip(
                PINNED_PARAMS.contract_terms, PINNED_PARAMS.contract_term_weights, strict=True
            )
        ),
        start=_ZERO,
    )

    opening_base_by_node: dict[NodeId, Decimal] = {}
    for node in _NODES:
        rows = [r for r in portfolio.raw_base_snapshot if r.node == node]
        opening_base_by_node[node] = max(rows, key=lambda r: int(r.period)).closing_base

    future_periods = [Period(int(cutoff) + 1 + k) for k in range(_FORECAST_HORIZON)]
    projected_base_by_node: dict[NodeId, dict[Period, Decimal]] = {}
    for node in _NODES:
        current = opening_base_by_node[node]
        by_period: dict[Period, Decimal] = {}
        for period in future_periods:
            movements = BaseMovements(
                node=node,
                period=period,
                closed_acquisition=projected_closed_by_node[node].get(period, _ZERO),
                churn=current * projected_churn_rate,
            )
            current = roll_forward_base(current, movements)
            by_period[period] = current
        projected_base_by_node[node] = by_period

    product_of: dict[str, str] = {
        str(node): product for node, product in PINNED_PARAMS.product_hierarchy.product_of.items()
    }
    rollup_by_period: dict[str, str] = {}
    for period in future_periods:
        leaf_values = {str(node): projected_base_by_node[node][period] for node in _NODES}
        parents = roll_up(leaf_values, product_of)
        (parent_total,) = parents.values()
        rollup_by_period[str(period)] = str(parent_total)

    return {
        "estimated_kernels": estimated_kernels,
        "estimated_regrade_transition": estimated_regrade_transition,
        "estimated_churn_hazard": {
            f"{tenure}|{term}": str(v)
            for (tenure, term), v in sorted(estimated_churn_hazard.items())
        },
        "projected_churn_rate": str(projected_churn_rate),
        "projected_base_by_node": {
            node: {str(p): str(v) for p, v in by_period.items()}
            for node, by_period in sorted(projected_base_by_node.items())
        },
        "rollup_flagship_by_period": rollup_by_period,
    }


def _serialize_order_event(e: Any) -> dict[str, Any]:
    return {
        "node": e.node,
        "order_channel": e.order_channel,
        "txn_type": str(e.txn_type),
        "raise_period": str(e.raise_period),
        "event_type": e.event_type,
        "event_period": str(e.event_period),
        "count": str(e.count),
        "to_node": e.to_node,
    }


def _serialize_subscription_event(e: Any) -> dict[str, Any]:
    return {
        "subscriber_id": e.subscriber_id,
        "event_type": e.event_type,
        "period": str(e.period),
        "node": e.node,
        "from_node": e.from_node,
        "acquisition_channel": e.acquisition_channel,
        "contract_term": e.contract_term,
        "tenure": e.tenure,
    }


def _serialize_base_snapshot(r: Any) -> dict[str, Any]:
    return {
        "node": r.node,
        "period": str(r.period),
        "opening_base": str(r.opening_base),
        "closing_base": str(r.closing_base),
        "closed_acquisition": str(r.closed_acquisition),
        "closed_resign_to": str(r.closed_resign_to),
        "closed_resign_from": str(r.closed_resign_from),
        "churn": str(r.churn),
        "migration_acq": str(r.migration_acq),
        "migration_churn": str(r.migration_churn),
    }


def _serialize_order_book_snapshot(r: Any) -> dict[str, Any]:
    return {
        "node": r.node,
        "order_channel": r.order_channel,
        "txn_type": str(r.txn_type),
        "period": str(r.period),
        "raised": str(r.raised),
        "closed": str(r.closed),
        "broken": str(r.broken),
        "open_orders": str(r.open_orders),
    }


def serialize_dataset(portfolio: SyntheticPortfolio) -> dict[str, Any]:
    return {
        "raw_order_event": [_serialize_order_event(e) for e in portfolio.raw_order_event],
        "raw_subscription_event": [
            _serialize_subscription_event(e) for e in portfolio.raw_subscription_event
        ],
        "raw_base_snapshot": [_serialize_base_snapshot(r) for r in portfolio.raw_base_snapshot],
        "raw_order_book_snapshot": [
            _serialize_order_book_snapshot(r) for r in portfolio.raw_order_book_snapshot
        ],
    }
