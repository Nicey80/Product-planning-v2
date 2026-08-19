"""Closure kernel application and order-book roll-forward (Identity 1).

    open_orders[t] = open_orders[t-1] + raised[t] - closed[t] - broken[t]

Breakage for a raise cohort is recognized in the same period as the raise
(a modeling choice documented in docs/domain-model.md section 3); closes
are spread forward across future periods according to g(k). Both g(k) and
breakage are non-negative and sum to exactly 1 per KernelSegment, so a
raise cohort's outstanding fraction is always in [0, 1] and reaches 0
after `kernel.max_lag` periods — this is what makes open_orders >= 0
(invariant 5) and cumulative closed + broken <= cumulative raised
(invariant 4) hold by construction, not just by empirical luck.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from engine.domain import KernelSegment, Period

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class ClosureResult:
    """Per-period order-pipeline outputs for one kernel segment."""

    raised: Mapping[Period, Decimal]
    closed: Mapping[Period, Decimal]
    broken: Mapping[Period, Decimal]
    open_orders: Mapping[Period, Decimal]

    def periods(self) -> tuple[Period, ...]:
        return tuple(sorted(self.open_orders))


def apply_closure_kernel(
    raised: Mapping[Period, Decimal],
    kernel: KernelSegment,
) -> ClosureResult:
    """Roll a raise series forward through a closure kernel.

    Returns closed[t], broken[t] and open_orders[t] (Identity 1) for every
    period from the first raise period through the last raise period plus
    the kernel's max lag (the point by which every raised order has
    resolved to closed or broken).
    """
    if not raised:
        return ClosureResult({}, {}, {}, {})

    raise_periods = sorted(raised)
    first = raise_periods[0]
    last = raise_periods[-1]
    horizon_end = Period(last + max(kernel.max_lag - 1, 0))

    all_periods = [Period(t) for t in range(first, horizon_end + 1)]
    closed: dict[Period, Decimal] = {t: _ZERO for t in all_periods}
    broken: dict[Period, Decimal] = {t: _ZERO for t in all_periods}

    for s in raise_periods:
        cohort = raised[s]
        if cohort == _ZERO:
            continue
        broken[Period(s)] = broken.get(Period(s), _ZERO) + cohort * kernel.breakage
        for k, g_k in enumerate(kernel.g):
            if g_k == _ZERO:
                continue
            t = Period(s + k)
            closed[t] = closed.get(t, _ZERO) + cohort * g_k

    open_orders: dict[Period, Decimal] = {}
    running = _ZERO
    for t in all_periods:
        running = running + raised.get(t, _ZERO) - closed[t] - broken[t]
        open_orders[t] = running

    return ClosureResult(dict(raised), closed, broken, open_orders)
