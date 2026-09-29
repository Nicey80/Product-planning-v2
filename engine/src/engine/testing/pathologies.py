"""Named, injectable pathology fixtures over engine/testing/synthetic.py.

Each function below builds (or mutates) a SyntheticPortfolio to exhibit one
specific real-world data-quality or modeling edge case, documented in its
own docstring together with what a test should assert about it. Three
different construction styles are used, matched to what each pathology
actually is:

- pure parameter variants (thin_volume_node, channel_launched_mid_history,
  mid_history_structural_break) hand back a mutated SyntheticParams -- the
  pathology is a property of the *true process*, so generation still goes
  through generate_portfolio.
- event-log injections (bulk_closure_batch, backdated_event,
  orphaned_orders) take an already-generated SyntheticPortfolio, append or
  alter raw_order_event/raw_subscription_event rows, and call
  rebuild_snapshots so both identities stay exact (or, for orphaned_orders,
  so each snapshot table stays internally exact even though the two event
  logs no longer agree with each other -- that disagreement *is* the
  pathology).
- right_censored_recent_cohorts and churn_with_open_regrade need no
  injection at all: the former is an inherent property of any generated
  portfolio near its horizon boundary, the latter is small and easiest to
  hand-build directly rather than mine out of a random run.
"""

from __future__ import annotations

import random
from dataclasses import replace
from decimal import Decimal

from engine.domain import ChannelId, NodeId, Period, TxnType
from engine.testing.synthetic import (
    ClosureProfile,
    OrderEvent,
    StructuralBreak,
    SubscriptionEvent,
    SyntheticParams,
    SyntheticPortfolio,
    build_base_snapshots,
    build_order_book_snapshots,
    generate_portfolio,
    make_channel_hierarchy,
    make_product_hierarchy,
    observation_cutoff,
    rebuild_snapshots,
)

_ZERO = Decimal(0)
_ONE = Decimal(1)


# ---------------------------------------------------------------------------
# 1. Right-censored recent raise cohorts
# ---------------------------------------------------------------------------


def pathology_right_censored_recent_cohorts(params: SyntheticParams) -> SyntheticPortfolio:
    """No injection needed: generate normally. Any cohort raised within a
    kernel segment's max_lag of the horizon's end hasn't had time to reach
    every age the kernel covers -- it's inherently right-censored, purely
    from the horizon/kernel relationship, not from anything unusual in the
    generating process. Pair with censored_raise_periods() to identify
    which raise periods are affected, and observation_cutoff(portfolio) as
    estimate_closure_kernel's cutoff. This is the fixture
    test_synthetic_recovery.py uses to prove estimate_closure_kernel is
    unbiased where naive_kernel_estimate is not.
    """
    return generate_portfolio(params)


def censored_raise_periods(
    portfolio: SyntheticPortfolio,
    *,
    node: NodeId,
    order_channel: ChannelId,
    txn_type: TxnType,
    max_age: int,
) -> tuple[Period, ...]:
    """Raise periods for this segment whose cohort has not yet been
    observed for `max_age` periods as of the portfolio's last period --
    i.e. still right-censored at that age."""
    cutoff = int(observation_cutoff(portfolio))
    raise_periods = {
        e.raise_period
        for e in portfolio.raw_order_event
        if e.node == node
        and e.order_channel == order_channel
        and e.txn_type == txn_type
        and e.event_type == "raised"
    }
    return tuple(sorted(p for p in raise_periods if int(p) + max_age - 1 > cutoff))


# ---------------------------------------------------------------------------
# 2. Bulk closure batch
# ---------------------------------------------------------------------------


def pathology_bulk_closure_batch(
    portfolio: SyntheticPortfolio,
    *,
    node: NodeId,
    order_channel: ChannelId,
    txn_type: TxnType,
    raise_period: Period,
    close_period: Period,
    count: Decimal,
    to_node: NodeId | None = None,
) -> SyntheticPortfolio:
    """An ops team clears a backlog of previously-raised orders in one
    large batch at `close_period`, independent of the kernel's normal
    pacing (e.g. a manual reconciliation project, not organic closure
    velocity). `txn_type` selects the base effect on close: ACQUISITION ->
    closed_acquisition, CHURN -> churn, REGRADE (`to_node` required) -> a
    resign_from/resign_to pair. Appends the closed OrderEvent and its
    matching SubscriptionEvent rows and rebuilds both snapshots, so both
    identities stay exact straight through the batch -- the "pathology" is
    just an unusually large single-period jump, not a broken identity.
    """
    if txn_type == TxnType.REGRADE and to_node is None:
        raise ValueError("a bulk regrade closure batch requires to_node")

    new_order_events = (
        *portfolio.raw_order_event,
        OrderEvent(
            node=node,
            order_channel=order_channel,
            txn_type=txn_type,
            raise_period=raise_period,
            event_type="closed",
            event_period=close_period,
            count=count,
            to_node=to_node,
        ),
    )

    term = str(portfolio.params.contract_terms[0])
    n = int(count)
    new_subscription_events = list(portfolio.raw_subscription_event)
    for i in range(n):
        sid = f"sub-batch-{node}-{int(close_period)}-{i:04d}"
        if txn_type == TxnType.ACQUISITION:
            new_subscription_events.append(
                SubscriptionEvent(
                    subscriber_id=sid,
                    event_type="acquired",
                    period=close_period,
                    node=node,
                    from_node=None,
                    acquisition_channel=order_channel,
                    contract_term=term,
                    tenure=0,
                )
            )
        elif txn_type == TxnType.CHURN:
            new_subscription_events.append(
                SubscriptionEvent(
                    subscriber_id=sid,
                    event_type="churned",
                    period=close_period,
                    node=node,
                    from_node=None,
                    acquisition_channel=order_channel,
                    contract_term=term,
                    tenure=int(close_period) - int(raise_period),
                )
            )
        else:  # REGRADE
            assert to_node is not None
            new_subscription_events.append(
                SubscriptionEvent(
                    subscriber_id=sid,
                    event_type="regraded",
                    period=close_period,
                    node=to_node,
                    from_node=node,
                    acquisition_channel=order_channel,
                    contract_term=term,
                    tenure=int(close_period) - int(raise_period),
                )
            )

    mutated = replace(
        portfolio,
        raw_order_event=new_order_events,
        raw_subscription_event=tuple(new_subscription_events),
    )
    return rebuild_snapshots(mutated)


# ---------------------------------------------------------------------------
# 3. Backdated events landing in a closed period
# ---------------------------------------------------------------------------


def pathology_backdated_event(
    portfolio: SyntheticPortfolio,
    *,
    node: NodeId,
    order_channel: ChannelId,
    txn_type: TxnType,
    raise_period: Period,
    closed_period: Period,
    count: Decimal,
    to_node: NodeId | None = None,
) -> SyntheticPortfolio:
    """A correction or late-arriving order, discovered and recorded after
    the fact, backdated to land in `closed_period` -- a period the rest of
    the dataset already treats as fully closed/reported (`closed_period`
    must be at or before observation_cutoff(portfolio)). Mechanically this
    is a bulk_closure_batch of one; what the fixture actually exercises is
    downstream: because raw_base_snapshot/raw_order_book_snapshot are
    always fully rebuilt from the event logs (see rebuild_snapshots), a
    backdated row is automatically applied to *its own* historical period,
    not smeared into "now" -- any consumer that instead incrementally
    caches snapshots period-by-period must explicitly invalidate
    closed_period forward when this happens.
    """
    if int(closed_period) > int(observation_cutoff(portfolio)):
        raise ValueError(
            "a backdated event must land at or before the portfolio's last observed period"
        )
    return pathology_bulk_closure_batch(
        portfolio,
        node=node,
        order_channel=order_channel,
        txn_type=txn_type,
        raise_period=raise_period,
        close_period=closed_period,
        count=count,
        to_node=to_node,
    )


# ---------------------------------------------------------------------------
# 4. Mid-history structural break
# ---------------------------------------------------------------------------


def pathology_mid_history_structural_break(
    params: SyntheticParams,
    *,
    break_period: int,
    acquisition_multiplier: Decimal = Decimal(2),
    churn_multiplier: Decimal = Decimal(2),
) -> SyntheticParams:
    """From `break_period` onward, true acquisition and churn rates shift
    by the given multipliers -- e.g. a pricing change or a competitor's
    exit mid-history. A test can estimate over the full history (which
    blends the two regimes) versus a window starting at break_period
    (which doesn't) to show the difference.
    """
    return replace(
        params,
        structural_break=StructuralBreak(
            break_period=break_period,
            acquisition_multiplier=acquisition_multiplier,
            churn_multiplier=churn_multiplier,
        ),
    )


# ---------------------------------------------------------------------------
# 5. Orphaned orders
# ---------------------------------------------------------------------------


def pathology_orphaned_orders(
    portfolio: SyntheticPortfolio,
    *,
    node: NodeId,
    order_channel: ChannelId,
    period: Period,
) -> SyntheticPortfolio:
    """Injects two independent reconciliation breaks at (node, period):

    1. an extra CLOSED acquisition OrderEvent with no matching "acquired"
       SubscriptionEvent -- the order pipeline says +1, the base never
       moved.
    2. an extra "churned" SubscriptionEvent with no matching OrderEvent --
       the base says -1, the order pipeline has no record backing it.

    Both raw_base_snapshot and raw_order_book_snapshot stay internally
    exact after rebuild_snapshots (each is rebuilt purely from its own
    event log, per Identity 1/2) -- the pathology is a break in agreement
    *between* the two logs, exactly the kind of silent reconciliation gap
    real order/subscriber pipelines produce, not a broken identity within
    either table.
    """
    term = str(portfolio.params.contract_terms[0])
    orphan_order = OrderEvent(
        node=node,
        order_channel=order_channel,
        txn_type=TxnType.ACQUISITION,
        raise_period=period,
        event_type="closed",
        event_period=period,
        count=_ONE,
    )
    orphan_subscription = SubscriptionEvent(
        subscriber_id=f"sub-orphan-churn-{node}-{int(period)}",
        event_type="churned",
        period=period,
        node=node,
        from_node=None,
        acquisition_channel=order_channel,
        contract_term=term,
        tenure=0,
    )
    mutated = replace(
        portfolio,
        raw_order_event=(*portfolio.raw_order_event, orphan_order),
        raw_subscription_event=(*portfolio.raw_subscription_event, orphan_subscription),
    )
    return rebuild_snapshots(mutated)


# ---------------------------------------------------------------------------
# 6. A subscriber churning while holding an open regrade order
# ---------------------------------------------------------------------------


def pathology_churn_with_open_regrade() -> SyntheticPortfolio:
    """A hand-built minimal scenario for a known real-world edge case: a
    subscriber raises a regrade, then churns *before* that regrade order
    closes. The regrade later resolves on its own original schedule and
    still posts its resign_from/resign_to pair, even though the subscriber
    already left the base.

    This is *not* a case where every invariant quietly survives: Identity
    2's roll-forward arithmetic still holds exactly at node_a (closing_base
    = opening_base - closed_resign_from, computed correctly), and Identity
    3 still holds (the resign_to at node_b exactly matches the resign_from
    at node_a, posted together) -- but invariant 5 (base >= 0) breaks,
    because the resign_from has nothing left to draw down: node_a's base
    goes to -1. That's the point of the fixture. Nothing in the roll-
    forward machinery *detects* a base movement referencing a subscriber
    who already left; a real pipeline needs an operational guard (break or
    cancel in-flight regrade/churn orders for a subscriber whose other
    open order resolves first) to prevent this, and a test against this
    fixture should assert the negative base appears, not that it doesn't.
    Self-contained: does not take or need a base SyntheticParams.
    """
    ph = make_product_hierarchy(groups=1, products_per_group=1, variants_per_product=2)
    ch = make_channel_hierarchy(groups=1, channels_per_group=1, sub_channels_per_channel=1)
    node_a, node_b = ph.nodes
    channel = ch.sub_channels[0]
    horizon = 8
    subscriber = "sub-churn-with-open-regrade"

    order_events = (
        OrderEvent(
            node=node_a,
            order_channel=channel,
            txn_type=TxnType.ACQUISITION,
            raise_period=Period(0),
            event_type="raised",
            event_period=Period(0),
            count=_ONE,
        ),
        OrderEvent(
            node=node_a,
            order_channel=channel,
            txn_type=TxnType.ACQUISITION,
            raise_period=Period(0),
            event_type="closed",
            event_period=Period(0),
            count=_ONE,
        ),
        OrderEvent(
            node=node_a,
            order_channel=channel,
            txn_type=TxnType.REGRADE,
            raise_period=Period(2),
            event_type="raised",
            event_period=Period(2),
            count=_ONE,
        ),
        OrderEvent(
            node=node_a,
            order_channel=channel,
            txn_type=TxnType.REGRADE,
            raise_period=Period(2),
            event_type="closed",
            event_period=Period(5),
            count=_ONE,
            to_node=node_b,
        ),
        OrderEvent(
            node=node_a,
            order_channel=channel,
            txn_type=TxnType.CHURN,
            raise_period=Period(3),
            event_type="raised",
            event_period=Period(3),
            count=_ONE,
        ),
        OrderEvent(
            node=node_a,
            order_channel=channel,
            txn_type=TxnType.CHURN,
            raise_period=Period(3),
            event_type="closed",
            event_period=Period(3),
            count=_ONE,
        ),
    )
    subscription_events = (
        SubscriptionEvent(
            subscriber_id=subscriber,
            event_type="acquired",
            period=Period(0),
            node=node_a,
            from_node=None,
            acquisition_channel=channel,
            contract_term="monthly",
            tenure=0,
        ),
        SubscriptionEvent(
            subscriber_id=subscriber,
            event_type="churned",
            period=Period(3),
            node=node_a,
            from_node=None,
            acquisition_channel=channel,
            contract_term="monthly",
            tenure=3,
        ),
        # the regrade still closes at t=5, on its original schedule, even
        # though the subscriber already churned at t=3:
        SubscriptionEvent(
            subscriber_id=subscriber,
            event_type="regraded",
            period=Period(5),
            node=node_b,
            from_node=node_a,
            acquisition_channel=channel,
            contract_term="monthly",
            tenure=5,
        ),
    )

    one_shot_profile = ClosureProfile(g=(_ONE,), breakage=_ZERO)
    product = ph.product_of[node_a]
    params = SyntheticParams(
        product_hierarchy=ph,
        channel_hierarchy=ch,
        horizon=horizon,
        seed=0,
        acquisition={},
        channel_mix={},
        contract_terms=(1,),
        contract_term_mix={channel: {1: _ONE}},
        regrade_transition={},
        regrade_hazard=lambda _ctx: _ZERO,
        churn_hazard=lambda _ctx: _ZERO,
        closure={
            (channel, TxnType.ACQUISITION, product): one_shot_profile,
            (channel, TxnType.REGRADE, product): one_shot_profile,
            (channel, TxnType.CHURN, product): one_shot_profile,
        },
        order_channel_weight={channel: _ONE},
    )

    return SyntheticPortfolio(
        params=params,
        raw_order_event=order_events,
        raw_subscription_event=subscription_events,
        raw_base_snapshot=build_base_snapshots(
            subscription_events, nodes=ph.nodes, horizon=horizon
        ),
        raw_order_book_snapshot=build_order_book_snapshots(
            order_events, nodes=ph.nodes, channels=ch.sub_channels, horizon=horizon
        ),
    )


# ---------------------------------------------------------------------------
# 7. A node with very thin volume
# ---------------------------------------------------------------------------


def pathology_thin_volume_node(
    params: SyntheticParams, *, node: NodeId, rate: Decimal = Decimal("0.05")
) -> SyntheticParams:
    """Collapses `node`'s acquisition process to a trickle (a constant
    mean of `rate` at every period), leaving every other node at its
    original process -- for testing estimator behavior (near-zero risk
    sets at some kernel ages, wide sampling noise) under sparse data.
    """
    process = params.acquisition.get(node)
    if process is None:
        return params
    thin_process = replace(process, mean_by_period=dict.fromkeys(process.mean_by_period, rate))
    new_acquisition = {**params.acquisition, node: thin_process}
    return replace(params, acquisition=new_acquisition)


# ---------------------------------------------------------------------------
# 8. A channel launched mid-history
# ---------------------------------------------------------------------------


def pathology_channel_launched_mid_history(
    params: SyntheticParams, *, channel: ChannelId, launch_period: int
) -> SyntheticParams:
    """`channel` carries no acquisitions, regrades, or churns before
    `launch_period` -- testing that estimators/rollups handle a channel
    with a shorter observation history than the rest of the portfolio
    without mistaking its pre-launch silence for zero true demand.
    """
    launch = dict(params.channel_launch_period)
    launch[channel] = launch_period
    return replace(params, channel_launch_period=launch)


# ---------------------------------------------------------------------------
# Rate-driven pathology knobs -- called once by generate_portfolio at the
# end of every run (see synthetic.py's _apply_pathology_rates), reading
# params.pathology_rates. These are conceptually the same mutations as
# this module's single-shot fixtures above (orphaned orders, backdated
# events, a bulk closure batch), but implemented directly here as one
# O(n) batch pass over the whole event log followed by exactly one
# rebuild_snapshots call, rather than calling those fixtures once per
# affected row -- at real-world portfolio scale (hundreds of thousands of
# events), an O(rows) number of rebuilds would be O(n^2).
# ---------------------------------------------------------------------------


def _any_pathology_active(rates: object) -> bool:
    from dataclasses import fields as _fields

    for f in _fields(rates):  # type: ignore[arg-type]
        value = getattr(rates, f.name)
        if isinstance(value, bool):
            if value:
                return True
        elif isinstance(value, Decimal) and value > _ZERO:
            return True
    return False


def apply_pathology_rates(portfolio: SyntheticPortfolio, rng: random.Random) -> SyntheticPortfolio:
    rates = portfolio.params.pathology_rates
    if not _any_pathology_active(rates):
        return portfolio

    order_events = list(portfolio.raw_order_event)
    subscription_events = list(portfolio.raw_subscription_event)
    default_term = portfolio.params.contract_terms[0] if portfolio.params.contract_terms else 0
    next_id = 0

    def orphan_subscriber_id() -> str:
        nonlocal next_id
        next_id += 1
        return f"sub-orphan-{next_id:08d}"

    # -- orphan_orders: closed with no matching base movement --
    if rates.orphan_closed_with_no_base_movement_rate > _ZERO:
        closed_acquisitions = [
            order_e
            for order_e in list(order_events)
            if order_e.event_type == "closed" and order_e.txn_type == TxnType.ACQUISITION
        ]
        for order_e in closed_acquisitions:
            if rng.random() < float(rates.orphan_closed_with_no_base_movement_rate):
                order_events.append(replace(order_e, count=_ONE))

    # -- orphan_orders: base movement with no matching order --
    if rates.orphan_base_movement_with_no_order_rate > _ZERO:
        acquired = [sub_e for sub_e in list(subscription_events) if sub_e.event_type == "acquired"]
        for sub_e in acquired:
            if rng.random() < float(rates.orphan_base_movement_with_no_order_rate):
                subscription_events.append(
                    SubscriptionEvent(
                        subscriber_id=orphan_subscriber_id(),
                        event_type="churned",
                        period=sub_e.period,
                        node=sub_e.node,
                        from_node=None,
                        acquisition_channel=sub_e.acquisition_channel,
                        contract_term=sub_e.contract_term,
                        tenure=0,
                    )
                )

    # -- backdated_events: a closed order re-recorded at an earlier period,
    # as if discovered/corrected after the fact --
    if rates.backdated_rate > _ZERO and rates.backdated_max_periods_back > 0:
        closed = [order_e for order_e in list(order_events) if order_e.event_type == "closed"]
        for order_e in closed:
            if rng.random() < float(rates.backdated_rate):
                back = rng.randint(1, rates.backdated_max_periods_back)
                backdated_period = Period(max(int(order_e.event_period) - back, 0))
                order_events.append(replace(order_e, event_period=backdated_period, count=_ONE))

    # -- zombie_orders: force some closed orders to instead have never
    # resolved at all -- drop the "closed" row, leaving the "raised" row
    # (still present) as permanently, unrealistically open --
    if rates.zombie_orders_rate > _ZERO:
        kept: list[OrderEvent] = []
        for order_e in order_events:
            if order_e.event_type == "closed" and rng.random() < float(rates.zombie_orders_rate):
                continue
            kept.append(order_e)
        order_events = kept

    # -- duplicate_orders: an exact duplicate raise+resolution pair for
    # the same cohort, as if the same order was recorded twice --
    if rates.duplicate_orders_rate > _ZERO:
        raised = [order_e for order_e in list(order_events) if order_e.event_type == "raised"]
        for order_e in raised:
            if rng.random() < float(rates.duplicate_orders_rate):
                order_events.append(replace(order_e, count=_ONE))
                matching_resolution = next(
                    (
                        r
                        for r in order_events
                        if r.event_type != "raised"
                        and r.node == order_e.node
                        and r.order_channel == order_e.order_channel
                        and r.txn_type == order_e.txn_type
                        and r.raise_period == order_e.raise_period
                    ),
                    None,
                )
                if matching_resolution is not None:
                    order_events.append(replace(matching_resolution, count=_ONE))

    # -- status_flapping: an order recorded closed, apparently reopened,
    # then closed again -- approximated at this aggregate (cohort-count)
    # grain as an extra "closed" row for the same cohort landing at a
    # later period than its true close, since there's no per-order
    # "reopened" event in this event vocabulary to model the transition
    # literally --
    if rates.status_flapping_rate > _ZERO:
        closed = [order_e for order_e in list(order_events) if order_e.event_type == "closed"]
        for order_e in closed:
            if rng.random() < float(rates.status_flapping_rate):
                flap_period = Period(int(order_e.event_period) + 1)
                order_events.append(replace(order_e, event_period=flap_period, count=_ONE))

    # -- bulk_closure_batch: a single large batch close of the oldest
    # still-open acquisition cohort in the portfolio, landing on the last
    # observed period, independent of the kernel's normal pacing --
    if rates.bulk_closure_batch_enabled:
        open_by_cohort: dict[tuple[NodeId, ChannelId, Period], Decimal] = {}
        for order_e in order_events:
            key = (order_e.node, order_e.order_channel, order_e.raise_period)
            if order_e.txn_type != TxnType.ACQUISITION:
                continue
            if order_e.event_type == "raised":
                open_by_cohort[key] = open_by_cohort.get(key, _ZERO) + order_e.count
            elif order_e.event_type in ("closed", "broken"):
                open_by_cohort[key] = open_by_cohort.get(key, _ZERO) - order_e.count
        oldest_open = [(k, v) for k, v in open_by_cohort.items() if v > _ZERO]
        if oldest_open:
            (node, channel, raise_period), count = min(oldest_open, key=lambda kv: int(kv[0][2]))
            last_period = Period(portfolio.params.horizon - 1)
            order_events.append(
                OrderEvent(
                    node=node,
                    order_channel=channel,
                    txn_type=TxnType.ACQUISITION,
                    raise_period=raise_period,
                    event_type="closed",
                    event_period=last_period,
                    count=count,
                )
            )
            for _ in range(int(count)):
                subscription_events.append(
                    SubscriptionEvent(
                        subscriber_id=orphan_subscriber_id(),
                        event_type="acquired",
                        period=last_period,
                        node=node,
                        from_node=None,
                        acquisition_channel=channel,
                        contract_term=str(default_term),
                        tenure=0,
                    )
                )

    mutated = replace(
        portfolio,
        raw_order_event=tuple(order_events),
        raw_subscription_event=tuple(subscription_events),
    )
    return rebuild_snapshots(mutated)
