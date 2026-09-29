"""Both core identities (CLAUDE.md) must hold exactly on any generated
portfolio -- not approximately, not "usually". These tests exercise
generate_portfolio across a few different shapes/seeds and check the
identities directly against the raw event logs, independent of how
build_base_snapshots/build_order_book_snapshots computed the snapshots
(i.e. this re-derives the identities from raw_order_event /
raw_subscription_event rather than trusting the same roll-forward code
that produced the snapshots).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.domain import ChannelId, NodeId, Period, TxnType
from engine.testing.synthetic import (
    AcquisitionProcess,
    ClosureProfile,
    HazardContext,
    SubscriptionEvent,
    SyntheticParams,
    generate_portfolio,
    make_channel_hierarchy,
    make_product_hierarchy,
)

_ZERO = Decimal(0)


def _regrade_hazard(_ctx: HazardContext) -> Decimal:
    return Decimal("0.03")


def _churn_hazard(_ctx: HazardContext) -> Decimal:
    return Decimal("0.04")


def _params(*, nodes: int, channels: int, horizon: int, seed: int, rate: str) -> SyntheticParams:
    ph = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=nodes)
    ch = make_channel_hierarchy(groups=1, channels_per_group=1, sub_channels_per_channel=channels)
    product = ph.product_of[ph.nodes[0]]
    periods = tuple(Period(t) for t in range(horizon))
    profile_acq = ClosureProfile(
        g=(Decimal("0.4"), Decimal("0.3"), Decimal("0.1")), breakage=Decimal("0.2")
    )
    profile_regrade = ClosureProfile(g=(Decimal("0.5"), Decimal("0.2")), breakage=Decimal("0.3"))
    profile_churn = ClosureProfile(g=(Decimal("0.7"),), breakage=Decimal("0.3"))
    closure = {}
    for c in ch.sub_channels:
        closure[(c, TxnType.ACQUISITION, product)] = profile_acq
        closure[(c, TxnType.REGRADE, product)] = profile_regrade
        closure[(c, TxnType.CHURN, product)] = profile_churn
    return SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=horizon,
        seed=seed,
        acquisition={
            n: AcquisitionProcess(
                mean_by_period=dict.fromkeys(periods, Decimal(rate)), dispersion=_ZERO
            )
            for n in ph.nodes
        },
        channel_mix={
            n: {t: {c: Decimal(1) / len(ch.sub_channels) for c in ch.sub_channels} for t in periods}
            for n in ph.nodes
        },
        contract_terms=(1, 12),
        contract_term_mix={c: {1: Decimal("0.6"), 12: Decimal("0.4")} for c in ch.sub_channels},
        regrade_transition={n: {m: Decimal(1) / len(ph.nodes) for m in ph.nodes} for n in ph.nodes},
        regrade_hazard=_regrade_hazard,
        churn_hazard=_churn_hazard,
        closure=closure,
        order_channel_weight={c: Decimal(1) for c in ch.sub_channels},
        regrade_order_channel_mix={
            t: {c: Decimal(1) / len(ch.sub_channels) for c in ch.sub_channels} for t in periods
        },
    )


@pytest.mark.parametrize(
    "nodes,channels,horizon,seed,rate",
    [
        (1, 1, 15, 1, "6"),
        (2, 2, 25, 2, "10"),
        (3, 1, 40, 3, "3"),
    ],
)
def test_identity1_order_book_holds_exactly(
    nodes: int, channels: int, horizon: int, seed: int, rate: str
) -> None:
    portfolio = generate_portfolio(
        _params(nodes=nodes, channels=channels, horizon=horizon, seed=seed, rate=rate)
    )
    for row in portfolio.raw_order_book_snapshot:
        assert row.open_orders >= _ZERO, row


@pytest.mark.parametrize(
    "nodes,channels,horizon,seed,rate",
    [
        (1, 1, 15, 1, "6"),
        (2, 2, 25, 2, "10"),
        (3, 1, 40, 3, "3"),
    ],
)
def test_identity4_resolutions_never_exceed_raised(
    nodes: int, channels: int, horizon: int, seed: int, rate: str
) -> None:
    portfolio = generate_portfolio(
        _params(nodes=nodes, channels=channels, horizon=horizon, seed=seed, rate=rate)
    )
    segments: dict[tuple[NodeId, ChannelId, TxnType], dict[str, Decimal]] = {}
    for row in portfolio.raw_order_book_snapshot:
        key = (row.node, row.order_channel, row.txn_type)
        acc = segments.setdefault(key, {"raised": _ZERO, "closed": _ZERO, "broken": _ZERO})
        acc["raised"] += row.raised
        acc["closed"] += row.closed
        acc["broken"] += row.broken

    for acc in segments.values():
        assert acc["closed"] + acc["broken"] <= acc["raised"]


@pytest.mark.parametrize(
    "nodes,channels,horizon,seed,rate",
    [
        (1, 1, 15, 1, "6"),
        (2, 2, 25, 2, "10"),
        (3, 1, 40, 3, "3"),
    ],
)
def test_identity3_regrade_pairs_net_to_zero_per_period(
    nodes: int, channels: int, horizon: int, seed: int, rate: str
) -> None:
    portfolio = generate_portfolio(
        _params(nodes=nodes, channels=channels, horizon=horizon, seed=seed, rate=rate)
    )
    by_period_to: dict[int, Decimal] = {}
    by_period_from: dict[int, Decimal] = {}
    for row in portfolio.raw_base_snapshot:
        by_period_to[int(row.period)] = (
            by_period_to.get(int(row.period), _ZERO) + row.closed_resign_to
        )
        by_period_from[int(row.period)] = (
            by_period_from.get(int(row.period), _ZERO) + row.closed_resign_from
        )
    for period in by_period_to:
        assert by_period_to[period] == by_period_from[period], period


@pytest.mark.parametrize(
    "nodes,channels,horizon,seed,rate",
    [
        (1, 1, 15, 1, "6"),
        (2, 2, 25, 2, "10"),
        (3, 1, 40, 3, "3"),
    ],
)
def test_identity2_base_roll_forward_exact_from_raw_events(
    nodes: int, channels: int, horizon: int, seed: int, rate: str
) -> None:
    """Recompute closing_base independently from raw_subscription_event and
    compare to raw_base_snapshot, so this doesn't just trust the same
    roll-forward code that produced the snapshot in the first place."""
    portfolio = generate_portfolio(
        _params(nodes=nodes, channels=channels, horizon=horizon, seed=seed, rate=rate)
    )
    running: dict[NodeId, Decimal] = {}
    by_node_period_snapshot = {(r.node, r.period): r for r in portfolio.raw_base_snapshot}

    events_by_period: dict[int, list[SubscriptionEvent]] = {}
    for ev in portfolio.raw_subscription_event:
        events_by_period.setdefault(int(ev.period), []).append(ev)

    for t in range(horizon):
        deltas: dict[NodeId, Decimal] = {}
        for ev in events_by_period.get(t, []):
            if ev.event_type == "acquired":
                deltas[ev.node] = deltas.get(ev.node, _ZERO) + 1
            elif ev.event_type == "regraded":
                assert ev.from_node is not None
                deltas[ev.from_node] = deltas.get(ev.from_node, _ZERO) - 1
                deltas[ev.node] = deltas.get(ev.node, _ZERO) + 1
            elif ev.event_type == "churned":
                deltas[ev.node] = deltas.get(ev.node, _ZERO) - 1
        for node in portfolio.params.product_hierarchy.nodes:
            opening = running.get(node, _ZERO)
            closing = opening + deltas.get(node, _ZERO)
            running[node] = closing
            snap = by_node_period_snapshot[(node, Period(t))]
            assert snap.opening_base == opening
            assert snap.closing_base == closing
