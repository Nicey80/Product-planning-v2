"""One test per named pathology fixture in engine/testing/pathologies.py.

Each test asserts the specific, documented property of its fixture -- see
the fixture's own docstring for the full explanation of what it represents
and why. These are deliberately not property tests: each pathology is a
concrete, hand-chosen scenario, not a random search over a space.
"""

from __future__ import annotations

from decimal import Decimal

from engine.domain import Period, TxnType
from engine.estimate import (
    naive_kernel_estimate,
    observations_from_order_events,
    raised_by_cohort_from_events,
)
from engine.testing.pathologies import (
    censored_raise_periods,
    pathology_backdated_event,
    pathology_bulk_closure_batch,
    pathology_channel_launched_mid_history,
    pathology_churn_with_open_regrade,
    pathology_mid_history_structural_break,
    pathology_orphaned_orders,
    pathology_right_censored_recent_cohorts,
    pathology_thin_volume_node,
)
from engine.testing.synthetic import (
    ClosureProfile,
    SyntheticParams,
    generate_portfolio,
    make_channel_hierarchy,
    make_product_hierarchy,
    observation_cutoff,
)

_ZERO = Decimal(0)


def _base_params(*, horizon: int = 20, seed: int = 100) -> SyntheticParams:
    ph = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=2)
    ch = make_channel_hierarchy(groups=1, channels_per_group=1, sub_channels_per_channel=1)
    nodes = ph.nodes
    channel = ch.sub_channels[0]
    profile = ClosureProfile(g=(Decimal("0.5"), Decimal("0.3")), breakage=Decimal("0.2"))
    return SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=horizon,
        seed=seed,
        acquisition_rate={(n, channel): Decimal("8") for n in nodes},
        contract_terms=("monthly",),
        contract_term_weights=(Decimal(1),),
        regrade_transition={n: {m: Decimal(1) / len(nodes) for m in nodes} for n in nodes},
        regrade_hazard=lambda _t, _c: Decimal("0.02"),
        churn_hazard=lambda _t, _c: Decimal("0.05"),
        closure={
            (channel, TxnType.ACQUISITION): profile,
            (channel, TxnType.REGRADE): ClosureProfile(
                g=(Decimal("0.6"),), breakage=Decimal("0.4")
            ),
            (channel, TxnType.CHURN): ClosureProfile(g=(Decimal("0.8"),), breakage=Decimal("0.2")),
        },
        order_channel_weight={channel: Decimal(1)},
    )


# ---------------------------------------------------------------------------
# 1. Right-censored recent raise cohorts
# ---------------------------------------------------------------------------


def test_right_censored_recent_cohorts_are_identified_and_open() -> None:
    params = _base_params(horizon=20)
    node = params.product_hierarchy.nodes[0]
    channel = params.channel_hierarchy.sub_channels[0]

    portfolio = pathology_right_censored_recent_cohorts(params)
    censored = censored_raise_periods(
        portfolio, node=node, order_channel=channel, txn_type=TxnType.ACQUISITION, max_age=2
    )

    # the very last raise period can't possibly have reached age 1 yet
    assert Period(19) in censored

    # every censored raise period should still show up as raised, with an
    # order book that hasn't fully drained by the horizon's end
    rows = {
        r.period: r
        for r in portfolio.raw_order_book_snapshot
        if r.node == node and r.order_channel == channel and r.txn_type == TxnType.ACQUISITION
    }
    assert rows[Period(19)].open_orders > _ZERO


# ---------------------------------------------------------------------------
# 2. Bulk closure batch
# ---------------------------------------------------------------------------


def test_bulk_closure_batch_lands_exactly_and_preserves_identities() -> None:
    params = _base_params(horizon=20)
    node = params.product_hierarchy.nodes[0]
    channel = params.channel_hierarchy.sub_channels[0]
    portfolio = generate_portfolio(params)

    before = next(
        r
        for r in portfolio.raw_order_book_snapshot
        if r.node == node
        and r.order_channel == channel
        and r.txn_type == TxnType.ACQUISITION
        and r.period == Period(15)
    )

    mutated = pathology_bulk_closure_batch(
        portfolio,
        node=node,
        order_channel=channel,
        txn_type=TxnType.ACQUISITION,
        raise_period=Period(1),
        close_period=Period(15),
        count=Decimal(40),
    )

    after = next(
        r
        for r in mutated.raw_order_book_snapshot
        if r.node == node
        and r.order_channel == channel
        and r.txn_type == TxnType.ACQUISITION
        and r.period == Period(15)
    )
    assert after.closed == before.closed + Decimal(40)

    base_after = next(
        r for r in mutated.raw_base_snapshot if r.node == node and r.period == Period(15)
    )
    assert base_after.closed_acquisition >= Decimal(40)

    # Identity 2 still holds exactly through the batch (roll_forward_base
    # guarantees this, but assert it explicitly at the batch's period)
    prev_base = next(
        r for r in mutated.raw_base_snapshot if r.node == node and r.period == Period(14)
    )
    assert base_after.opening_base == prev_base.closing_base
    assert base_after.closing_base == (
        base_after.opening_base
        + base_after.closed_acquisition
        + base_after.closed_resign_to
        - base_after.closed_resign_from
        - base_after.churn
        + base_after.migration_acq
        - base_after.migration_churn
    )


# ---------------------------------------------------------------------------
# 3. Backdated events landing in a closed period
# ---------------------------------------------------------------------------


def test_backdated_event_applies_to_its_own_historical_period() -> None:
    params = _base_params(horizon=20)
    node = params.product_hierarchy.nodes[0]
    channel = params.channel_hierarchy.sub_channels[0]
    portfolio = generate_portfolio(params)
    cutoff = observation_cutoff(portfolio)
    assert cutoff == Period(19)

    backdated_period = Period(5)
    before = next(
        r for r in portfolio.raw_base_snapshot if r.node == node and r.period == backdated_period
    )

    mutated = pathology_backdated_event(
        portfolio,
        node=node,
        order_channel=channel,
        txn_type=TxnType.CHURN,
        raise_period=Period(4),
        closed_period=backdated_period,
        count=Decimal(3),
    )

    after = next(
        r for r in mutated.raw_base_snapshot if r.node == node and r.period == backdated_period
    )
    assert after.churn == before.churn + Decimal(3)

    # periods before the backdated correction are untouched
    before_period = Period(4)
    orig_prior = next(
        r for r in portfolio.raw_base_snapshot if r.node == node and r.period == before_period
    )
    mutated_prior = next(
        r for r in mutated.raw_base_snapshot if r.node == node and r.period == before_period
    )
    assert orig_prior == mutated_prior


# ---------------------------------------------------------------------------
# 4. Mid-history structural break
# ---------------------------------------------------------------------------


def test_structural_break_shifts_acquisition_rate_only_after_break_period() -> None:
    base = _base_params(horizon=40, seed=77)
    broken = pathology_mid_history_structural_break(
        base, break_period=20, acquisition_multiplier=Decimal(4), churn_multiplier=Decimal(1)
    )

    portfolio = generate_portfolio(broken)
    node = base.product_hierarchy.nodes[0]
    channel = base.channel_hierarchy.sub_channels[0]

    raised_by_period = {
        e.raise_period: e.count
        for e in portfolio.raw_order_event
        if e.node == node
        and e.order_channel == channel
        and e.txn_type == TxnType.ACQUISITION
        and e.event_type == "raised"
    }
    pre_break_avg = (
        sum((raised_by_period.get(Period(t), _ZERO) for t in range(20)), start=_ZERO) / 20
    )
    post_break_avg = (
        sum((raised_by_period.get(Period(t), _ZERO) for t in range(20, 40)), start=_ZERO) / 20
    )

    # true rate is 8/period pre-break, ~32/period post-break -- a >2x jump
    # should be unmistakable even with Poisson noise at these volumes.
    assert post_break_avg > pre_break_avg * Decimal("2")


# ---------------------------------------------------------------------------
# 5. Orphaned orders
# ---------------------------------------------------------------------------


def test_orphaned_orders_break_cross_table_agreement_not_either_identity() -> None:
    params = _base_params(horizon=20)
    node = params.product_hierarchy.nodes[0]
    channel = params.channel_hierarchy.sub_channels[0]
    portfolio = generate_portfolio(params)
    period = Period(7)

    mutated = pathology_orphaned_orders(portfolio, node=node, order_channel=channel, period=period)

    order_closed_acq = sum(
        e.count
        for e in mutated.raw_order_event
        if e.node == node
        and e.event_type == "closed"
        and e.txn_type == TxnType.ACQUISITION
        and e.event_period == period
    )
    base_row = next(r for r in mutated.raw_base_snapshot if r.node == node and r.period == period)
    # the injected orphan closed order has no matching subscription event,
    # so the order log and the base snapshot disagree by exactly 1.
    assert order_closed_acq == base_row.closed_acquisition + Decimal(1)

    churned_subscription_events = sum(
        1
        for e in mutated.raw_subscription_event
        if e.node == node and e.event_type == "churned" and e.period == period
    )
    order_churn_closed = sum(
        e.count
        for e in mutated.raw_order_event
        if e.node == node
        and e.event_type == "closed"
        and e.txn_type == TxnType.CHURN
        and e.event_period == period
    )
    assert churned_subscription_events == order_churn_closed + 1

    # each snapshot table is still internally exact: base roll-forward
    # holds regardless of what the order log says.
    prev = next(
        r
        for r in mutated.raw_base_snapshot
        if r.node == node and r.period == Period(int(period) - 1)
    )
    assert base_row.opening_base == prev.closing_base
    assert base_row.closing_base == (
        base_row.opening_base
        + base_row.closed_acquisition
        + base_row.closed_resign_to
        - base_row.closed_resign_from
        - base_row.churn
        + base_row.migration_acq
        - base_row.migration_churn
    )


# ---------------------------------------------------------------------------
# 6. A subscriber churning while holding an open regrade order
# ---------------------------------------------------------------------------


def test_churn_with_open_regrade_posts_pair_but_breaks_nonnegativity() -> None:
    portfolio = pathology_churn_with_open_regrade()
    node_a, node_b = portfolio.params.product_hierarchy.nodes

    churn_row = next(
        r for r in portfolio.raw_base_snapshot if r.node == node_a and r.period == Period(3)
    )
    assert churn_row.closing_base == Decimal(-0)  # churned to 0, as expected

    regrade_close_row_a = next(
        r for r in portfolio.raw_base_snapshot if r.node == node_a and r.period == Period(5)
    )
    regrade_close_row_b = next(
        r for r in portfolio.raw_base_snapshot if r.node == node_b and r.period == Period(5)
    )

    # Identity 3: the pair still posts together, same count, same period.
    assert (
        regrade_close_row_a.closed_resign_from == regrade_close_row_b.closed_resign_to == Decimal(1)
    )

    # Identity 2's arithmetic holds exactly even here...
    assert regrade_close_row_a.closing_base == regrade_close_row_a.opening_base - Decimal(1)

    # ...but invariant 5 (base >= 0) does not: this is the pathology.
    assert regrade_close_row_a.closing_base < _ZERO


# ---------------------------------------------------------------------------
# 7. A node with very thin volume
# ---------------------------------------------------------------------------


def test_thin_volume_node_has_sparse_data_other_nodes_dont() -> None:
    params = _base_params(horizon=30, seed=9)
    node0, node1 = params.product_hierarchy.nodes
    channel = params.channel_hierarchy.sub_channels[0]

    thin = pathology_thin_volume_node(params, node=node0, rate=Decimal("0.02"))
    portfolio = generate_portfolio(thin)

    raised_thin = sum(
        e.count
        for e in portfolio.raw_order_event
        if e.node == node0
        and e.order_channel == channel
        and e.txn_type == TxnType.ACQUISITION
        and e.event_type == "raised"
    )
    raised_normal = sum(
        e.count
        for e in portfolio.raw_order_event
        if e.node == node1
        and e.order_channel == channel
        and e.txn_type == TxnType.ACQUISITION
        and e.event_type == "raised"
    )
    assert raised_thin < raised_normal / 10


# ---------------------------------------------------------------------------
# 8. A channel launched mid-history
# ---------------------------------------------------------------------------


def test_channel_launched_mid_history_has_no_activity_before_launch() -> None:
    params = _base_params(horizon=30, seed=3)
    channel = params.channel_hierarchy.sub_channels[0]

    launched = pathology_channel_launched_mid_history(params, channel=channel, launch_period=15)
    portfolio = generate_portfolio(launched)

    pre_launch = [
        e
        for e in portfolio.raw_order_event
        if e.order_channel == channel and e.event_type == "raised" and int(e.raise_period) < 15
    ]
    post_launch = [
        e
        for e in portfolio.raw_order_event
        if e.order_channel == channel and e.event_type == "raised" and int(e.raise_period) >= 15
    ]
    assert pre_launch == []
    assert post_launch != []


# ---------------------------------------------------------------------------
# A quick cross-check that the naive estimator really is unusable on the
# censored fixture -- test_synthetic_recovery.py has the full proof; this
# just confirms the pathology-fixture entry point wires to the same data.
# ---------------------------------------------------------------------------


def test_right_censored_fixture_feeds_naive_estimator_a_visible_bias() -> None:
    ph = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=1)
    ch = make_channel_hierarchy(groups=1, channels_per_group=1, sub_channels_per_channel=1)
    node = ph.nodes[0]
    channel = ch.sub_channels[0]
    true_g = tuple(Decimal("0.1") for _ in range(6))
    profile = ClosureProfile(g=true_g, breakage=Decimal(1) - sum(true_g, start=_ZERO))

    params = SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=9,
        seed=42,
        acquisition_rate={(node, channel): Decimal("60")},
        contract_terms=("monthly",),
        contract_term_weights=(Decimal(1),),
        regrade_transition={},
        regrade_hazard=lambda _t, _c: _ZERO,
        churn_hazard=lambda _t, _c: _ZERO,
        closure={(channel, TxnType.ACQUISITION): profile},
        order_channel_weight={channel: Decimal(1)},
    )
    portfolio = pathology_right_censored_recent_cohorts(params)

    observations = observations_from_order_events(
        portfolio.raw_order_event, node=node, order_channel=channel, txn_type=TxnType.ACQUISITION
    )
    raised = raised_by_cohort_from_events(
        portfolio.raw_order_event, node=node, order_channel=channel, txn_type=TxnType.ACQUISITION
    )
    naive = naive_kernel_estimate(observations, raised, max_age=6)
    assert abs(naive[5] - Decimal("0.1")) > Decimal("0.03")
