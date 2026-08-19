"""Rate-based base simulation.

Used to exercise Identity 2 under a construction that guarantees
non-negativity (invariant 5): outflows (resign_from, churn,
migration_churn) are expressed as rates in [0, 1] applied to the opening
base of their period and jointly capped at 1, so a period's total
outflow can never exceed what's available in the base.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from engine.base import BaseMovements, roll_forward_base
from engine.domain import NodeId, Period

_ZERO = Decimal(0)
_ONE = Decimal(1)


def simulate_node_base(
    node: NodeId,
    opening_base: Decimal,
    periods: Sequence[Period],
    closed_acquisition: Mapping[Period, Decimal] | None = None,
    closed_resign_to: Mapping[Period, Decimal] | None = None,
    resign_from_rate: Mapping[Period, Decimal] | None = None,
    churn_rate: Mapping[Period, Decimal] | None = None,
    migration_acq: Mapping[Period, Decimal] | None = None,
    migration_churn_rate: Mapping[Period, Decimal] | None = None,
) -> dict[Period, Decimal]:
    """Roll a single node's base forward period by period.

    Returns closing_base per period, computed via roll_forward_base
    (Identity 2) at every step. Rate parameters must be in [0, 1]; their
    sum for a given period is capped at 1 (scaled down proportionally if
    it would otherwise exceed 1) so outflows never exceed the opening
    base of that period -- this is what keeps the result non-negative by
    construction rather than by chance.
    """
    closed_acquisition = closed_acquisition or {}
    closed_resign_to = closed_resign_to or {}
    resign_from_rate = resign_from_rate or {}
    churn_rate = churn_rate or {}
    migration_acq = migration_acq or {}
    migration_churn_rate = migration_churn_rate or {}

    closing: dict[Period, Decimal] = {}
    current = opening_base
    for t in periods:
        r_resign = resign_from_rate.get(t, _ZERO)
        r_churn = churn_rate.get(t, _ZERO)
        r_mig = migration_churn_rate.get(t, _ZERO)
        total_out_rate = r_resign + r_churn + r_mig
        scale = _ONE if total_out_rate <= _ONE else _ONE / total_out_rate

        movements = BaseMovements(
            node=node,
            period=t,
            closed_acquisition=closed_acquisition.get(t, _ZERO),
            closed_resign_to=closed_resign_to.get(t, _ZERO),
            closed_resign_from=current * r_resign * scale,
            churn=current * r_churn * scale,
            migration_acq=migration_acq.get(t, _ZERO),
            migration_churn=current * r_mig * scale,
        )
        current = roll_forward_base(current, movements)
        closing[t] = current
    return closing
