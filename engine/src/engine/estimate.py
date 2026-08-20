"""Empirical estimators for forecast-run inputs: the closure kernel g(k)
and breakage rate, the regrade transition matrix, and churn hazards by
tenure and contract term.

Per docs/domain-model.md section 3, `g` "is typically estimated empirically
from historical raise->close lags and refreshed per forecast run; it is a
run input, not a hardcoded constant." This module is that estimation step.
It consumes the same raw event shapes engine/testing/synthetic.py produces
(and that a real order/subscription event warehouse would produce), and
its whole purpose is validated by parameter recovery: generate data from
known parameters, estimate, and check the estimate lands close to the
truth (engine/tests/test_synthetic_recovery.py).

Unbiasedness under right-censoring
-----------------------------------
A raise cohort observed only recently hasn't had time to reach every age.
A naive `closed(k) / raised` ratio (naive_kernel_estimate below) counts
that cohort fully in the denominator at every age k, including ages it
hasn't had time to reach yet -- which silently pulls every g(k) toward
zero as k grows, worst for the most recent cohorts. estimate_closure_kernel
instead uses a discrete-time life-table (actuarial) estimator: at each age
k, both the numerator (closes at age k) and the denominator (units still
at risk going into age k) are restricted to cohorts that have actually
been *observed* to age k as of `observation_cutoff`. A cohort too young to
have reached age k is simply excluded from that age's estimate rather than
counted as a non-close. estimate_churn_hazard applies the identical
construction to subscriber tenure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from engine.domain import ChannelId, KernelSegment, NodeId, Period, TxnType

_ZERO = Decimal(0)
_ONE = Decimal(1)


# ---------------------------------------------------------------------------
# Closure kernel / breakage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrderObservation:
    """One raise cohort's resolution at one age: `count` units raised in
    `raise_period` resolved (closed or broken) `age` periods later."""

    raise_period: Period
    age: int
    resolution: str  # "closed" | "broken"
    count: Decimal


def observations_from_order_events(
    events: Sequence[object],
    *,
    node: NodeId,
    order_channel: ChannelId,
    txn_type: TxnType,
) -> list[OrderObservation]:
    """Filter a raw_order_event log (engine.testing.synthetic.OrderEvent
    rows, or anything with the same attributes) down to one segment's
    resolution observations. "raised" rows are not observations of a
    resolution and are excluded -- use raised_by_cohort for the raise
    series."""
    out: list[OrderObservation] = []
    for e in events:
        if e.node != node or e.order_channel != order_channel or e.txn_type != txn_type:  # type: ignore[attr-defined]
            continue
        if e.event_type == "raised":  # type: ignore[attr-defined]
            continue
        out.append(
            OrderObservation(
                raise_period=e.raise_period,  # type: ignore[attr-defined]
                age=e.age,  # type: ignore[attr-defined]
                resolution=e.event_type,  # type: ignore[attr-defined]
                count=e.count,  # type: ignore[attr-defined]
            )
        )
    return out


def raised_by_cohort_from_events(
    events: Sequence[object],
    *,
    node: NodeId,
    order_channel: ChannelId,
    txn_type: TxnType,
) -> dict[Period, Decimal]:
    """The raise series for one segment, keyed by raise_period."""
    out: dict[Period, Decimal] = {}
    for e in events:
        if (
            e.node == node  # type: ignore[attr-defined]
            and e.order_channel == order_channel  # type: ignore[attr-defined]
            and e.txn_type == txn_type  # type: ignore[attr-defined]
            and e.event_type == "raised"  # type: ignore[attr-defined]
        ):
            out[e.raise_period] = out.get(e.raise_period, _ZERO) + e.count  # type: ignore[attr-defined]
    return out


def estimate_closure_kernel(
    observations: Sequence[OrderObservation],
    raised_by_cohort: Mapping[Period, Decimal],
    *,
    node: NodeId,
    order_channel: ChannelId,
    txn_type: TxnType,
    observation_cutoff: Period,
    max_age: int,
) -> KernelSegment:
    """Discrete-time life-table estimate of g(k) and breakage.

    At each age k, the risk set is restricted to cohorts `s` with
    `s + k <= observation_cutoff` (i.e. actually observed to age k) and the
    survivors of that cohort after ages < k. g(k) is the unconditional
    probability of closing exactly at age k: the product of surviving every
    earlier age's hazard, times age k's close hazard. breakage is taken as
    the residual `1 - sum(g)` so the KernelSegment's exact-sum-to-one
    contract (invariant 2) holds regardless of estimation noise, rather
    than as a separately-accumulated (and therefore only approximately
    consistent) quantity.
    """
    cohorts = sorted(raised_by_cohort)
    closed_by_cohort_age: dict[Period, dict[int, Decimal]] = {s: {} for s in cohorts}
    broken_by_cohort_age: dict[Period, dict[int, Decimal]] = {s: {} for s in cohorts}
    for obs in observations:
        bucket = closed_by_cohort_age if obs.resolution == "closed" else broken_by_cohort_age
        by_age = bucket.setdefault(obs.raise_period, {})
        by_age[obs.age] = by_age.get(obs.age, _ZERO) + obs.count

    g: list[Decimal] = []
    survival = _ONE
    for k in range(max_age):
        at_risk = _ZERO
        n_closed = _ZERO
        n_broken = _ZERO
        for s in cohorts:
            if int(s) + k > int(observation_cutoff):
                continue  # cohort hasn't been observed to age k yet
            resolved_before = sum(
                closed_by_cohort_age[s].get(j, _ZERO) + broken_by_cohort_age[s].get(j, _ZERO)
                for j in range(k)
            )
            remaining = raised_by_cohort[s] - resolved_before
            at_risk += remaining
            n_closed += closed_by_cohort_age[s].get(k, _ZERO)
            n_broken += broken_by_cohort_age[s].get(k, _ZERO)

        if at_risk <= _ZERO:
            g.append(_ZERO)
            continue

        close_hazard = n_closed / at_risk
        break_hazard = n_broken / at_risk
        g.append(survival * close_hazard)
        survival *= _ONE - close_hazard - break_hazard

    breakage = _ONE - sum(g, start=_ZERO)
    return KernelSegment(
        node=node,
        order_channel=order_channel,
        txn_type=txn_type,
        g=tuple(g),
        breakage=breakage,
    )


def naive_kernel_estimate(
    observations: Sequence[OrderObservation],
    raised_by_cohort: Mapping[Period, Decimal],
    *,
    max_age: int,
) -> tuple[Decimal, ...]:
    """The biased comparison estimator: g_naive(k) = total closed at age k
    (across every cohort, regardless of whether it has been observed that
    long) / total raised. Exists only so tests can demonstrate the bias
    estimate_closure_kernel's life-table construction avoids -- never use
    this for a real run's kernel input."""
    total_raised = sum(raised_by_cohort.values(), start=_ZERO)
    closed_at = dict.fromkeys(range(max_age), _ZERO)
    for obs in observations:
        if obs.resolution == "closed" and obs.age < max_age:
            closed_at[obs.age] += obs.count
    if total_raised <= _ZERO:
        return tuple(_ZERO for _ in range(max_age))
    return tuple(closed_at[k] / total_raised for k in range(max_age))


# ---------------------------------------------------------------------------
# Regrade transition matrix
# ---------------------------------------------------------------------------


def estimate_regrade_transition(
    subscription_events: Sequence[object],
) -> dict[NodeId, dict[NodeId, Decimal]]:
    """Empirical P(dest | source) from raw_subscription_event "regraded"
    rows: dest_counts[source][dest] / sum(dest_counts[source])."""
    counts: dict[NodeId, dict[NodeId, Decimal]] = {}
    for ev in subscription_events:
        if ev.event_type != "regraded":  # type: ignore[attr-defined]
            continue
        source = ev.from_node  # type: ignore[attr-defined]
        dest = ev.node  # type: ignore[attr-defined]
        counts.setdefault(source, {})
        counts[source][dest] = counts[source].get(dest, _ZERO) + _ONE

    result: dict[NodeId, dict[NodeId, Decimal]] = {}
    for source, dest_counts in counts.items():
        total = sum(dest_counts.values(), start=_ZERO)
        result[source] = (
            {dest: c / total for dest, c in dest_counts.items()} if total > _ZERO else {}
        )
    return result


# ---------------------------------------------------------------------------
# Churn hazard by tenure and contract term
# ---------------------------------------------------------------------------


def estimate_churn_hazard(
    subscription_events: Sequence[object],
    *,
    observation_cutoff: Period,
    max_tenure: int,
) -> dict[tuple[int, str], Decimal]:
    """Discrete-time life-table hazard(tenure, contract_term): among
    subscribers acquired with that contract_term who have been observed to
    reach that tenure by `observation_cutoff` and who hadn't already
    churned at an earlier tenure, the fraction who churn exactly at that
    tenure. Subscribers not yet old enough to have reached a given tenure
    are excluded from that tenure's risk set (the same right-censoring fix
    as estimate_closure_kernel)."""
    acquired: dict[str, tuple[int, str]] = {}
    churned_tenure: dict[str, int] = {}
    for ev in subscription_events:
        if ev.event_type == "acquired":  # type: ignore[attr-defined]
            acquired[ev.subscriber_id] = (int(ev.period), ev.contract_term)  # type: ignore[attr-defined]
        elif ev.event_type == "churned":  # type: ignore[attr-defined]
            churned_tenure[ev.subscriber_id] = ev.tenure  # type: ignore[attr-defined]

    terms = sorted({term for _, term in acquired.values()})
    hazards: dict[tuple[int, str], Decimal] = {}
    for term in terms:
        cohort = [(sid, acq_period) for sid, (acq_period, t) in acquired.items() if t == term]
        for k in range(max_tenure):
            at_risk = _ZERO
            n_churn = _ZERO
            for sid, acq_period in cohort:
                if acq_period + k > int(observation_cutoff):
                    continue  # not yet observed to reach tenure k
                churn_k = churned_tenure.get(sid)
                if churn_k is not None and churn_k < k:
                    continue  # already churned before reaching tenure k
                at_risk += _ONE
                if churn_k == k:
                    n_churn += _ONE
            hazards[(k, term)] = (n_churn / at_risk) if at_risk > _ZERO else _ZERO
    return hazards
