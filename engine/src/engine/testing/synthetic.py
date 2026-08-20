"""Portfolio simulator: generates a complete, internally consistent
synthetic dataset from known ground-truth parameters.

This is the counterpart to engine/estimate.py. The whole point of this
module is parameter *recovery*: generate data from a known acquisition
process, regrade transition matrix, churn hazard surface, and closure
kernel; run the estimators in engine/estimate.py over the generated raw
event log; assert the estimates land close to the truth. See
engine/tests/test_synthetic_recovery.py.

Design choice -- events, not snapshots, are primary:
`raw_order_event` (the order pipeline log) and `raw_subscription_event`
(the subscriber lineage log: acquired / regraded / churned) are generated
directly by the simulation. `raw_base_snapshot` and `raw_order_book_snapshot`
are then *derived* deterministically from those two event logs by
build_base_snapshots/build_order_book_snapshots, which are thin wrappers
around engine.base.roll_forward_base and the Identity 1 roll-forward,
respectively. Both core identities therefore hold **by construction**, not
by post-hoc validation -- and pathology fixtures that inject or mutate
events and re-run the two builders inherit that guarantee (or, for the
"orphaned orders" pathology, deliberately violate the cross-*table*
agreement between the two event logs while each snapshot individually
stays internally consistent -- see pathology_orphaned_orders).

Determinism: generate_portfolio is a pure function of `SyntheticParams`
(which carries its own `seed`) -- no wall-clock reads, no hidden global
state -- per CLAUDE.md's engine-purity convention. Re-running with the same
params reproduces byte-identical output.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

from engine.base import BaseMovements, merge_movements, roll_forward_base
from engine.domain import ChannelId, NodeId, Period, TxnType

_ZERO = Decimal(0)
_ONE = Decimal(1)

# ---------------------------------------------------------------------------
# Hierarchies
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProductHierarchy:
    """variant (= Node, leaf) -> product -> product_group."""

    nodes: tuple[NodeId, ...]
    product_of: Mapping[NodeId, str]
    group_of_product: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ChannelHierarchy:
    """sub_channel (leaf) -> channel -> channel_group."""

    sub_channels: tuple[ChannelId, ...]
    channel_of: Mapping[ChannelId, str]
    group_of_channel: Mapping[str, str]


def make_product_hierarchy(
    *, groups: int, products_per_group: int, variants_per_product: int
) -> ProductHierarchy:
    nodes: list[NodeId] = []
    product_of: dict[NodeId, str] = {}
    group_of_product: dict[str, str] = {}
    for g in range(groups):
        group = f"pg{g}"
        for p in range(products_per_group):
            product = f"{group}-p{p}"
            group_of_product[product] = group
            for v in range(variants_per_product):
                node = NodeId(f"{product}-v{v}")
                nodes.append(node)
                product_of[node] = product
    return ProductHierarchy(tuple(nodes), product_of, group_of_product)


def make_channel_hierarchy(
    *, groups: int, channels_per_group: int, sub_channels_per_channel: int
) -> ChannelHierarchy:
    subs: list[ChannelId] = []
    channel_of: dict[ChannelId, str] = {}
    group_of_channel: dict[str, str] = {}
    for g in range(groups):
        group = f"cg{g}"
        for c in range(channels_per_group):
            channel = f"{group}-c{c}"
            group_of_channel[channel] = group
            for s in range(sub_channels_per_channel):
                sub = ChannelId(f"{channel}-s{s}")
                subs.append(sub)
                channel_of[sub] = channel
    return ChannelHierarchy(tuple(subs), channel_of, group_of_channel)


# ---------------------------------------------------------------------------
# Ground-truth parameters
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClosureProfile:
    """True closure kernel g(k) and breakage for one (order_channel,
    txn_type) segment, applied uniformly across nodes ("closure hazards and
    breakage rates by channel and order type" per the module's brief)."""

    g: tuple[Decimal, ...]
    breakage: Decimal

    def __post_init__(self) -> None:
        total = sum(self.g, start=_ZERO) + self.breakage
        if abs(total - _ONE) > Decimal("1e-9"):
            raise ValueError(f"sum(g) + breakage must equal 1, got {total}")

    @property
    def max_lag(self) -> int:
        return len(self.g)


@dataclass(frozen=True, slots=True)
class StructuralBreak:
    """A deliberate mid-history shift in the true generating process,
    applied to acquisition and churn from `break_period` onward. This is
    what the "mid-history structural break" pathology fixture uses."""

    break_period: int
    acquisition_multiplier: Decimal = _ONE
    churn_multiplier: Decimal = _ONE


HazardFn = Callable[[int, str], Decimal]
"""(tenure_in_periods, contract_term) -> probability in [0, 1] that the
corresponding order (regrade or churn) is raised for an at-risk subscriber
in a given period."""


@dataclass(frozen=True, slots=True)
class SyntheticParams:
    """The complete ground-truth parameter set for one synthetic portfolio."""

    product_hierarchy: ProductHierarchy
    channel_hierarchy: ChannelHierarchy
    horizon: int
    seed: int

    # true acquisition process per node x channel: mean raises per period
    # (Poisson rate). Missing (node, channel) pairs are treated as rate 0.
    acquisition_rate: Mapping[tuple[NodeId, ChannelId], Decimal]

    contract_terms: tuple[str, ...]
    contract_term_weights: tuple[Decimal, ...]

    # true regrade transition matrix: P(dest | source, a regrade happens).
    # Each source's destination distribution must sum to 1; a node absent
    # from this mapping never regrades.
    regrade_transition: Mapping[NodeId, Mapping[NodeId, Decimal]]

    regrade_hazard: HazardFn
    churn_hazard: HazardFn

    # true closure hazards/breakage by (order_channel, txn_type).
    closure: Mapping[tuple[ChannelId, TxnType], ClosureProfile]

    # relative weight used to sample the order_channel of a regrade/churn
    # order (acquisitions use acquisition_rate's node x channel grain
    # directly). A channel's weight is ignored before its launch period.
    order_channel_weight: Mapping[ChannelId, Decimal]

    initial_base: Mapping[NodeId, Decimal] = field(default_factory=dict)
    channel_launch_period: Mapping[ChannelId, int] = field(default_factory=dict)
    structural_break: StructuralBreak | None = None

    def __post_init__(self) -> None:
        if len(self.contract_terms) != len(self.contract_term_weights):
            raise ValueError("contract_terms and contract_term_weights must be same length")
        for source, dist in self.regrade_transition.items():
            total = sum(dist.values(), start=_ZERO)
            if dist and abs(total - _ONE) > Decimal("1e-9"):
                raise ValueError(f"regrade_transition[{source}] must sum to 1, got {total}")

    def is_channel_launched(self, channel: ChannelId, period: int) -> bool:
        return period >= self.channel_launch_period.get(channel, 0)


# ---------------------------------------------------------------------------
# Raw outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """One row of the order pipeline log: a raise, a close, or a break."""

    node: NodeId
    order_channel: ChannelId
    txn_type: TxnType
    raise_period: Period
    event_type: str  # "raised" | "closed" | "broken"
    event_period: Period
    count: Decimal
    to_node: NodeId | None = None  # regrade destination, "closed" rows only

    @property
    def age(self) -> int:
        return int(self.event_period) - int(self.raise_period)


@dataclass(frozen=True, slots=True)
class SubscriptionEvent:
    """One row of the subscriber lineage log. This is the source of truth
    for base movements: build_base_snapshots derives Identity 2 purely from
    these rows."""

    subscriber_id: str
    event_type: str  # "acquired" | "regraded" | "churned"
    period: Period
    node: NodeId  # node *after* the event (destination, for regrades)
    from_node: NodeId | None  # source node, "regraded" rows only
    acquisition_channel: ChannelId
    contract_term: str
    tenure: int  # periods since acquisition, at the time of this event


@dataclass(frozen=True, slots=True)
class BaseSnapshot:
    node: NodeId
    period: Period
    opening_base: Decimal
    closing_base: Decimal
    closed_acquisition: Decimal
    closed_resign_to: Decimal
    closed_resign_from: Decimal
    churn: Decimal
    migration_acq: Decimal
    migration_churn: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    node: NodeId
    order_channel: ChannelId
    txn_type: TxnType
    period: Period
    raised: Decimal
    closed: Decimal
    broken: Decimal
    open_orders: Decimal


@dataclass(frozen=True, slots=True)
class SyntheticPortfolio:
    params: SyntheticParams
    raw_order_event: tuple[OrderEvent, ...]
    raw_subscription_event: tuple[SubscriptionEvent, ...]
    raw_base_snapshot: tuple[BaseSnapshot, ...]
    raw_order_book_snapshot: tuple[OrderBookSnapshot, ...]


def observation_cutoff(portfolio: SyntheticPortfolio) -> Period:
    """The last period for which this portfolio has data -- horizon - 1.
    Cohorts raised close to this cutoff are inherently right-censored: the
    kernel's max_lag can extend past it, so recent cohorts haven't had time
    to fully resolve. Pass this to estimate_closure_kernel's
    observation_cutoff so it excludes them from the risk set at ages they
    haven't reached yet, per docstring in engine/estimate.py."""
    return Period(portfolio.params.horizon - 1)


# ---------------------------------------------------------------------------
# Snapshot builders -- shared by generate_portfolio and pathology injectors.
# Both are pure functions of an event list: re-run them after mutating
# events and the identities still hold by construction.
# ---------------------------------------------------------------------------


def build_order_book_snapshots(
    events: Sequence[OrderEvent],
    *,
    nodes: Sequence[NodeId],
    channels: Sequence[ChannelId],
    horizon: int,
) -> tuple[OrderBookSnapshot, ...]:
    """Roll Identity 1 forward per (node, order_channel, txn_type) from a
    raw_order_event log: open_orders[t] = open_orders[t-1] + raised[t] -
    closed[t] - broken[t]."""
    segments: set[tuple[NodeId, ChannelId, TxnType]] = {
        (e.node, e.order_channel, e.txn_type) for e in events
    }
    raised_by: dict[tuple[NodeId, ChannelId, TxnType, int], Decimal] = {}
    closed_by: dict[tuple[NodeId, ChannelId, TxnType, int], Decimal] = {}
    broken_by: dict[tuple[NodeId, ChannelId, TxnType, int], Decimal] = {}
    for e in events:
        key = (e.node, e.order_channel, e.txn_type, int(e.event_period))
        if e.event_type == "raised":
            raised_by[key] = raised_by.get(key, _ZERO) + e.count
        elif e.event_type == "closed":
            closed_by[key] = closed_by.get(key, _ZERO) + e.count
        elif e.event_type == "broken":
            broken_by[key] = broken_by.get(key, _ZERO) + e.count
        else:
            raise ValueError(f"unknown event_type {e.event_type!r}")

    out: list[OrderBookSnapshot] = []
    for node, channel, txn_type in sorted(segments):
        running = _ZERO
        for t in range(horizon):
            key = (node, channel, txn_type, t)
            raised = raised_by.get(key, _ZERO)
            closed = closed_by.get(key, _ZERO)
            broken = broken_by.get(key, _ZERO)
            running = running + raised - closed - broken
            out.append(
                OrderBookSnapshot(
                    node=node,
                    order_channel=channel,
                    txn_type=txn_type,
                    period=Period(t),
                    raised=raised,
                    closed=closed,
                    broken=broken,
                    open_orders=running,
                )
            )
    return tuple(out)


def build_base_snapshots(
    subscription_events: Sequence[SubscriptionEvent],
    *,
    nodes: Sequence[NodeId],
    horizon: int,
    initial_base: Mapping[NodeId, Decimal] | None = None,
) -> tuple[BaseSnapshot, ...]:
    """Derive Identity 2 movements from a raw_subscription_event log (one
    row per subscriber-level base-affecting event) and roll the base
    forward via engine.base.roll_forward_base."""
    initial_base = initial_base or {}
    movements_by_node_period: dict[tuple[NodeId, int], list[BaseMovements]] = {}

    def add(node: NodeId, period: int, **kwargs: Decimal) -> None:
        movements_by_node_period.setdefault((node, period), []).append(
            BaseMovements(node=node, period=Period(period), **kwargs)
        )

    for ev in subscription_events:
        if ev.event_type == "acquired":
            add(ev.node, int(ev.period), closed_acquisition=_ONE)
        elif ev.event_type == "regraded":
            assert ev.from_node is not None
            add(ev.from_node, int(ev.period), closed_resign_from=_ONE)
            add(ev.node, int(ev.period), closed_resign_to=_ONE)
        elif ev.event_type == "churned":
            add(ev.node, int(ev.period), churn=_ONE)
        else:
            raise ValueError(f"unknown event_type {ev.event_type!r}")

    out: list[BaseSnapshot] = []
    for node in sorted(nodes):
        current = initial_base.get(node, _ZERO)
        for t in range(horizon):
            rows = movements_by_node_period.get((node, t), [])
            movements = (
                merge_movements(node, Period(t), rows)
                if rows
                else BaseMovements(node=node, period=Period(t))
            )
            opening = current
            current = roll_forward_base(current, movements)
            out.append(
                BaseSnapshot(
                    node=node,
                    period=Period(t),
                    opening_base=opening,
                    closing_base=current,
                    closed_acquisition=movements.closed_acquisition,
                    closed_resign_to=movements.closed_resign_to,
                    closed_resign_from=movements.closed_resign_from,
                    churn=movements.churn,
                    migration_acq=movements.migration_acq,
                    migration_churn=movements.migration_churn,
                )
            )
    return tuple(out)


def rebuild_snapshots(portfolio: SyntheticPortfolio) -> SyntheticPortfolio:
    """Recompute both snapshot tables from a portfolio's (possibly mutated)
    event logs. Used by pathology injectors after they append/alter rows --
    both identities hold in the rebuilt result by the same construction as
    generate_portfolio itself, since it's the same two builder functions."""
    params = portfolio.params
    return replace(
        portfolio,
        raw_base_snapshot=build_base_snapshots(
            portfolio.raw_subscription_event,
            nodes=params.product_hierarchy.nodes,
            horizon=params.horizon,
            initial_base=params.initial_base,
        ),
        raw_order_book_snapshot=build_order_book_snapshots(
            portfolio.raw_order_event,
            nodes=params.product_hierarchy.nodes,
            channels=params.channel_hierarchy.sub_channels,
            horizon=params.horizon,
        ),
    )


# ---------------------------------------------------------------------------
# Stochastic primitives (stdlib random only -- engine/ stays dependency-free)
# ---------------------------------------------------------------------------


def _poisson(rng: random.Random, mean: Decimal) -> int:
    """Knuth's algorithm. Adequate for the modest per-period rates a
    synthetic portfolio uses (no need for numpy)."""
    lam = float(mean)
    if lam <= 0:
        return 0
    limit = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= limit:
            return k - 1


def _binomial(rng: random.Random, n: int, p: float) -> int:
    if n <= 0 or p <= 0:
        return 0
    if p >= 1:
        return n
    return sum(1 for _ in range(n) if rng.random() < p)


def _multinomial_split(rng: random.Random, n: int, probs: Sequence[Decimal]) -> list[int]:
    """Exact multinomial draw via sequential conditional binomials: counts
    always sum to exactly n, so raise-cohort conservation (Identity 1 /
    invariant 4) holds by construction rather than by rounding luck."""
    remaining_n = n
    remaining_p = _ONE
    counts: list[int] = []
    for p in probs[:-1]:
        if remaining_n <= 0 or remaining_p <= _ZERO:
            counts.append(0)
            continue
        cond_p = min(float(p / remaining_p), 1.0)
        k = _binomial(rng, remaining_n, cond_p)
        counts.append(k)
        remaining_n -= k
        remaining_p -= p
    counts.append(remaining_n)
    return counts


def _weighted_choice(rng: random.Random, items: Sequence[str], weights: Sequence[Decimal]) -> str:
    return rng.choices(items, weights=[float(w) for w in weights], k=1)[0]


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


@dataclass
class _LiveSubscriber:
    node: NodeId
    acquisition_channel: ChannelId
    contract_term: str
    acquired_period: int
    open_regrade: bool = False
    open_churn: bool = False


def generate_portfolio(params: SyntheticParams) -> SyntheticPortfolio:
    """Simulate a full portfolio period by period from `params.seed`.

    Order pipeline: each raised unit's ultimate resolution (which future
    period it closes in, or that it breaks) is drawn once, at raise time,
    from the segment's ClosureProfile via an exact multinomial split --
    breakage is recognized in the raise period itself (matching
    engine.kernel's convention), closes are spread across raise_period + k
    for k in range(len(g)). Regrade/churn candidates are drawn from the
    live subscriber population using per-subscriber tenure, so the
    generated data has genuine tenure-dependent churn/regrade structure for
    the estimators in engine/estimate.py to recover.
    """
    rng = random.Random(params.seed)
    nodes = params.product_hierarchy.nodes
    horizon = params.horizon

    order_events: list[OrderEvent] = []
    subscription_events: list[SubscriptionEvent] = []

    # tasks keyed by the period they take effect in
    pending_acquisitions: dict[int, list[tuple[NodeId, ChannelId, int]]] = {}
    pending_regrade_closes: dict[int, list[tuple[str, NodeId, NodeId, ChannelId]]] = {}
    pending_churn_closes: dict[int, list[tuple[str, NodeId, ChannelId]]] = {}

    live: dict[str, _LiveSubscriber] = {}
    next_subscriber_id = 0

    def new_subscriber_id() -> str:
        nonlocal next_subscriber_id
        sid = f"sub-{next_subscriber_id:08d}"
        next_subscriber_id += 1
        return sid

    def closure_profile(channel: ChannelId, txn_type: TxnType) -> ClosureProfile:
        try:
            return params.closure[(channel, txn_type)]
        except KeyError:
            raise KeyError(
                f"no ClosureProfile configured for (order_channel={channel!r}, "
                f"txn_type={txn_type!r})"
            ) from None

    def schedule_resolution(
        node: NodeId,
        channel: ChannelId,
        txn_type: TxnType,
        raise_period: int,
        n: int,
        *,
        on_close: Callable[[int, int], None],
        on_break: Callable[[int], None] | None = None,
    ) -> None:
        """Split n raised units per the segment's ClosureProfile, emit
        closed/broken OrderEvent rows, and invoke on_close(resolve_period,
        count) for each nonzero closed bucket (and on_break(count) for the
        broken bucket, processed first) so the caller can schedule the
        base-affecting side effect / release any "open order" hold."""
        profile = closure_profile(channel, txn_type)
        probs = [*profile.g, profile.breakage]
        counts = _multinomial_split(rng, n, probs)
        broken_count = counts[-1]
        if broken_count:
            order_events.append(
                OrderEvent(
                    node=node,
                    order_channel=channel,
                    txn_type=txn_type,
                    raise_period=Period(raise_period),
                    event_type="broken",
                    event_period=Period(raise_period),
                    count=Decimal(broken_count),
                )
            )
            if on_break is not None:
                on_break(broken_count)
        for k, closed_count in enumerate(counts[:-1]):
            if not closed_count:
                continue
            resolve_period = raise_period + k
            if resolve_period >= horizon:
                # Still open as of the portfolio's last observed period --
                # right-censored. No "closed" row exists yet: recording one
                # would mean the dataset knows something that, as of
                # horizon - 1, hasn't happened. The unresolved units simply
                # stay counted in open_orders via their "raised" row.
                continue
            order_events.append(
                OrderEvent(
                    node=node,
                    order_channel=channel,
                    txn_type=txn_type,
                    raise_period=Period(raise_period),
                    event_type="closed",
                    event_period=Period(resolve_period),
                    count=Decimal(closed_count),
                )
            )
            on_close(resolve_period, closed_count)

    def sample_order_channel(period: int) -> ChannelId:
        eligible = [
            c
            for c in params.channel_hierarchy.sub_channels
            if params.is_channel_launched(c, period)
            and params.order_channel_weight.get(c, _ZERO) > 0
        ]
        weights = [params.order_channel_weight[c] for c in eligible]
        return ChannelId(_weighted_choice(rng, eligible, weights))

    def sample_contract_term() -> str:
        return _weighted_choice(
            rng, list(params.contract_terms), list(params.contract_term_weights)
        )

    for t in range(horizon):
        # -- resolve tasks scheduled to land in this period (may include
        # this same period's raises for lag-0 closes, since raising happens
        # before resolution below) --

        # -- acquisitions raised this period --
        acq_multiplier = _ONE
        if params.structural_break and t >= params.structural_break.break_period:
            acq_multiplier = params.structural_break.acquisition_multiplier
        for node in nodes:
            for channel in params.channel_hierarchy.sub_channels:
                if not params.is_channel_launched(channel, t):
                    continue
                rate = params.acquisition_rate.get((node, channel), _ZERO) * acq_multiplier
                if rate <= 0:
                    continue
                n = _poisson(rng, rate)
                if n <= 0:
                    continue
                order_events.append(
                    OrderEvent(
                        node=node,
                        order_channel=channel,
                        txn_type=TxnType.ACQUISITION,
                        raise_period=Period(t),
                        event_type="raised",
                        event_period=Period(t),
                        count=Decimal(n),
                    )
                )

                def on_close_acquisition(
                    resolve_period: int,
                    count: int,
                    node: NodeId = node,
                    channel: ChannelId = channel,
                ) -> None:
                    pending_acquisitions.setdefault(resolve_period, []).append(
                        (node, channel, count)
                    )

                schedule_resolution(
                    node, channel, TxnType.ACQUISITION, t, n, on_close=on_close_acquisition
                )

        # -- regrade / churn candidates drawn from the live population --
        churn_multiplier = _ONE
        if params.structural_break and t >= params.structural_break.break_period:
            churn_multiplier = params.structural_break.churn_multiplier

        churn_candidates: list[str] = []
        regrade_candidates: list[str] = []
        for sid in sorted(live):
            rec = live[sid]
            if rec.open_regrade or rec.open_churn:
                continue
            tenure = t - rec.acquired_period
            p_churn = min(
                float(params.churn_hazard(tenure, rec.contract_term) * churn_multiplier), 1.0
            )
            p_regrade = float(params.regrade_hazard(tenure, rec.contract_term))
            u = rng.random()
            if u < p_churn:
                churn_candidates.append(sid)
            elif u < p_churn + p_regrade:
                regrade_candidates.append(sid)

        # group by (node, order_channel) so the multinomial split is drawn
        # once per segment, exactly like acquisitions.
        churn_groups: dict[tuple[NodeId, ChannelId], list[str]] = {}
        for sid in churn_candidates:
            rec = live[sid]
            rec.open_churn = True
            channel = sample_order_channel(t)
            churn_groups.setdefault((rec.node, channel), []).append(sid)

        for (node, channel), sids in churn_groups.items():
            order_events.append(
                OrderEvent(
                    node=node,
                    order_channel=channel,
                    txn_type=TxnType.CHURN,
                    raise_period=Period(t),
                    event_type="raised",
                    event_period=Period(t),
                    count=Decimal(len(sids)),
                )
            )
            rng.shuffle(sids)

            def on_close_churn(
                resolve_period: int,
                count: int,
                sids: list[str] = sids,
                node: NodeId = node,
                channel: ChannelId = channel,
            ) -> None:
                batch, sids[:count] = sids[:count], []
                for sid in batch:
                    pending_churn_closes.setdefault(resolve_period, []).append((sid, node, channel))

            def on_break_churn(count: int, sids: list[str] = sids) -> None:
                batch, sids[:count] = sids[:count], []
                for sid in batch:
                    live[sid].open_churn = False

            schedule_resolution(
                node,
                channel,
                TxnType.CHURN,
                t,
                len(sids),
                on_close=on_close_churn,
                on_break=on_break_churn,
            )

        regrade_groups: dict[tuple[NodeId, ChannelId], list[tuple[str, NodeId]]] = {}
        for sid in regrade_candidates:
            rec = live[sid]
            dest_dist = params.regrade_transition.get(rec.node, {})
            if not dest_dist:
                continue
            rec.open_regrade = True
            dest = NodeId(_weighted_choice(rng, list(dest_dist.keys()), list(dest_dist.values())))
            channel = sample_order_channel(t)
            regrade_groups.setdefault((rec.node, channel), []).append((sid, dest))

        for (node, channel), pairs in regrade_groups.items():
            order_events.append(
                OrderEvent(
                    node=node,
                    order_channel=channel,
                    txn_type=TxnType.REGRADE,
                    raise_period=Period(t),
                    event_type="raised",
                    event_period=Period(t),
                    count=Decimal(len(pairs)),
                )
            )
            rng.shuffle(pairs)

            def on_close_regrade(
                resolve_period: int,
                count: int,
                pairs: list[tuple[str, NodeId]] = pairs,
                node: NodeId = node,
                channel: ChannelId = channel,
            ) -> None:
                batch, pairs[:count] = pairs[:count], []
                for sid, dest in batch:
                    pending_regrade_closes.setdefault(resolve_period, []).append(
                        (sid, node, dest, channel)
                    )

            def on_break_regrade(count: int, pairs: list[tuple[str, NodeId]] = pairs) -> None:
                batch, pairs[:count] = pairs[:count], []
                for sid, _dest in batch:
                    live[sid].open_regrade = False

            schedule_resolution(
                node,
                channel,
                TxnType.REGRADE,
                t,
                len(pairs),
                on_close=on_close_regrade,
                on_break=on_break_regrade,
            )

        # -- apply this period's resolutions to the live population / base --
        for node, channel, count in pending_acquisitions.pop(t, []):
            for _ in range(count):
                sid = new_subscriber_id()
                term = sample_contract_term()
                live[sid] = _LiveSubscriber(
                    node=node, acquisition_channel=channel, contract_term=term, acquired_period=t
                )
                subscription_events.append(
                    SubscriptionEvent(
                        subscriber_id=sid,
                        event_type="acquired",
                        period=Period(t),
                        node=node,
                        from_node=None,
                        acquisition_channel=channel,
                        contract_term=term,
                        tenure=0,
                    )
                )

        for sid, from_node, dest, _channel in pending_regrade_closes.pop(t, []):
            resolving = live.get(sid)
            if resolving is None:
                continue  # churned before this regrade closed -- see pathology docs
            resolving.node = dest
            resolving.open_regrade = False
            subscription_events.append(
                SubscriptionEvent(
                    subscriber_id=sid,
                    event_type="regraded",
                    period=Period(t),
                    node=dest,
                    from_node=from_node,
                    acquisition_channel=resolving.acquisition_channel,
                    contract_term=resolving.contract_term,
                    tenure=t - resolving.acquired_period,
                )
            )

        for sid, node, _channel in pending_churn_closes.pop(t, []):
            if sid not in live:
                continue
            resolved = live.pop(sid)
            subscription_events.append(
                SubscriptionEvent(
                    subscriber_id=sid,
                    event_type="churned",
                    period=Period(t),
                    node=node,
                    from_node=None,
                    acquisition_channel=resolved.acquisition_channel,
                    contract_term=resolved.contract_term,
                    tenure=t - resolved.acquired_period,
                )
            )

    base_snapshots = build_base_snapshots(
        subscription_events, nodes=nodes, horizon=horizon, initial_base=params.initial_base
    )
    order_book_snapshots = build_order_book_snapshots(
        order_events, nodes=nodes, channels=params.channel_hierarchy.sub_channels, horizon=horizon
    )

    return SyntheticPortfolio(
        params=params,
        raw_order_event=tuple(order_events),
        raw_subscription_event=tuple(subscription_events),
        raw_base_snapshot=base_snapshots,
        raw_order_book_snapshot=order_book_snapshots,
    )
