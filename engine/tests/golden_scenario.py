"""A fixed, deterministic scenario exercising the closure kernel, base
roll-forward, and hierarchy roll-up together end to end.

This is not a property test (see test_invariants.py for those) and not a
narrow unit test -- it is a regression fence: `test_golden_run.py`
recomputes this scenario on every CI run and diffs it byte-for-byte
against the committed snapshot in golden/golden_run_v1.json. Any change
to engine/ that shifts a single number anywhere in the pipeline fails
CI's golden-run-snapshot stage, forcing a deliberate, reviewed snapshot
update rather than a silent behavior drift.

All inputs are exact Decimal literals, so the result is exact -- no
floating-point tolerance is needed anywhere in the comparison.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from engine.domain import ChannelId, KernelSegment, NodeId, Period, TxnType
from engine.hierarchy import cross_margin_total, roll_up
from engine.kernel import apply_closure_kernel
from engine.simulate import simulate_node_base

NODE_MONTHLY = NodeId("core-monthly")
NODE_ANNUAL = NodeId("core-annual")
CHANNEL_WEB = ChannelId("web")

KERNEL = KernelSegment(
    node=NODE_MONTHLY,
    order_channel=CHANNEL_WEB,
    txn_type=TxnType.ACQUISITION,
    g=(Decimal("0.6"), Decimal("0.3")),
    breakage=Decimal("0.1"),
)

RAISED: dict[Period, Decimal] = {
    Period(0): Decimal(100),
    Period(1): Decimal(120),
    Period(2): Decimal(90),
    Period(3): Decimal(110),
}

PARENT_OF: dict[str, str] = {NODE_MONTHLY: "flagship", NODE_ANNUAL: "flagship"}


def _decimals(mapping: dict[Period, Decimal]) -> dict[str, str]:
    return {str(period): str(value) for period, value in sorted(mapping.items())}


def run_golden_scenario() -> dict[str, Any]:
    closure = apply_closure_kernel(RAISED, KERNEL)
    periods = closure.periods()

    base_monthly = simulate_node_base(
        node=NODE_MONTHLY,
        opening_base=Decimal(500),
        periods=periods,
        closed_acquisition=closure.closed,
        resign_from_rate=dict.fromkeys(periods, Decimal("0.03")),
        churn_rate=dict.fromkeys(periods, Decimal("0.02")),
    )
    base_annual = simulate_node_base(
        node=NODE_ANNUAL,
        opening_base=Decimal(50),
        periods=periods,
        closed_resign_to={Period(2): Decimal(15)},
        churn_rate=dict.fromkeys(periods, Decimal("0.01")),
    )

    rollup_by_period = {
        str(period): str(
            roll_up(
                {NODE_MONTHLY: base_monthly[period], NODE_ANNUAL: base_annual[period]},
                PARENT_OF,
            )["flagship"]
        )
        for period in periods
    }

    last = periods[-1]
    cross_margin = cross_margin_total(
        {
            (NODE_MONTHLY, CHANNEL_WEB): base_monthly[last],
            (NODE_ANNUAL, CHANNEL_WEB): base_annual[last],
        }
    )

    return {
        "raised": _decimals(RAISED),
        "closed": _decimals(dict(closure.closed)),
        "broken": _decimals(dict(closure.broken)),
        "open_orders": _decimals(dict(closure.open_orders)),
        "base_monthly": _decimals(base_monthly),
        "base_annual": _decimals(base_annual),
        "rollup_flagship_by_period": rollup_by_period,
        "cross_margin_total_last_period": str(cross_margin),
    }
