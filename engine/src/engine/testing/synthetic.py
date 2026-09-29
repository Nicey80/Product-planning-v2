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

from engine.base import (
    BaseMovements,
    MigrationPair,
    merge_movements,
    migration_pair_movements,
    roll_forward_base,
)
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


@dataclass(frozen=True, slots=True)
class AcquisitionProcess:
    """Resolved (not raw-config) negative-binomial acquisition process for
    one node: mean count per period -- already folding in level, trend,
    seasonality, and regressor elasticities -- plus a constant
    overdispersion parameter. Built by engine.testing.config; the
    generator just samples from it."""

    mean_by_period: Mapping[Period, Decimal]
    dispersion: Decimal


EXTEND_SAME = NodeId("_extend_same")
"""Sentinel regrade-transition destination meaning "extend the current
contract without changing node" -- a pure contract extension per
docs/domain-model.md section 5.1 (from_node == to_node), which also always
resets contract_started_period. Usable as an ordinary regrade_transition
dict key since NodeId is just a str NewType."""

OFF_PORTFOLIO_SINK = NodeId("_off_portfolio")
"""Virtual destination node for an off-portfolio migration exit (spec's
`to_node: null`) -- pairs the source's migration_churn against a
migration_acq here rather than leaving it one-sided, per
docs/domain-model.md section 5.2's "a pair that still nets to zero across
whatever scope the correction is defined over". Excluded from
product-hierarchy rollups: callers filter it out (or roll_up raises,
since it has no product_of entry) before rolling base totals up the
hierarchy."""


@dataclass(frozen=True, slots=True)
class MigrationSchedule:
    """A dated migration schedule: `schedule[period]` subscribers move
    from `from_node` to `to_node` in that period, bypassing the order
    pipeline entirely (invariant 8). `to_node=None` is an off-portfolio
    exit, paired against OFF_PORTFOLIO_SINK -- see its docstring."""

    name: str
    from_node: NodeId
    to_node: NodeId | None
    schedule: Mapping[Period, Decimal]

    @property
    def resolved_to_node(self) -> NodeId:
        return self.to_node if self.to_node is not None else OFF_PORTFOLIO_SINK


@dataclass(frozen=True, slots=True)
class PathologyRates:
    """Continuous data-quality pathology knobs applied during generation,
    as opposed to the one-off fixtures in pathologies.py (which these
    reuse under the hood). Each rate is a per-applicable-row probability;
    0 (the default) means off. `structural_break` has its own
    SyntheticParams field (predates this dataclass) rather than living
    here; its "enabled" toggle is simply `structural_break is not None`."""

    orphan_closed_with_no_base_movement_rate: Decimal = _ZERO
    orphan_base_movement_with_no_order_rate: Decimal = _ZERO
    backdated_rate: Decimal = _ZERO
    backdated_max_periods_back: int = 0
    status_flapping_rate: Decimal = _ZERO
    bulk_closure_batch_enabled: bool = False
    zombie_orders_rate: Decimal = _ZERO
    duplicate_orders_rate: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class SelfCheckConfig:
    """Which invariants generate_portfolio verifies before returning --
    all default True (these should always hold by construction; this is
    defense in depth, not a way to relax the guarantee). Matches the
    spec's `output.self_check` block one for one."""

    assert_order_book_identity: bool = True
    assert_base_identity: bool = True
    assert_resign_conservation: bool = True
    assert_kernel_sums_to_one: bool = True
    assert_non_negative_base: bool = True
    assert_migrations_net_zero_when_paired: bool = True


@dataclass(frozen=True, slots=True)
class HazardContext:
    """Everything a churn/regrade HazardFn needs to evaluate a rate for
    one live subscriber in one period. Built by generate_portfolio at
    evaluation time. Calendar-derived fields (month-of-year, for
    seasonality) are deliberately NOT included: the hazard closures built
    by engine.testing.config already capture whatever calendar they need,
    which keeps this module -- and every HazardContext consumer -- calendar
    -agnostic, per Period's "opaque integer index" contract."""

    period: Period
    tenure: int  # periods since acquisition -- keys early-life effects
    months_since_contract_start: int  # keys contract-expiry-window effects
    contract_term: int  # contract length, in periods (e.g. months)
    acquisition_channel: ChannelId
    node: NodeId


HazardFn = Callable[[HazardContext], Decimal]
"""HazardContext -> probability in [0, 1] that the corresponding order
(regrade or churn) is raised for an at-risk subscriber in a given period."""


@dataclass(frozen=True, slots=True)
class SyntheticParams:
    """The complete ground-truth parameter set for one synthetic portfolio."""

    product_hierarchy: ProductHierarchy
    channel_hierarchy: ChannelHierarchy
    horizon: int
    seed: int

    # true acquisition process per node: a negative-binomial count process
    # (already resolved to a per-period mean -- see AcquisitionProcess).
    acquisition: Mapping[NodeId, AcquisitionProcess]

    # compositional (sums to 1 each period), per-node, channel split of
    # each node's acquisitions -- already resolved including any drift.
    channel_mix: Mapping[NodeId, Mapping[Period, Mapping[ChannelId, Decimal]]]

    # contract lengths, in periods (e.g. months), and each channel's
    # resolved share distribution across them (sums to 1 per channel).
    contract_terms: tuple[int, ...]
    contract_term_mix: Mapping[ChannelId, Mapping[int, Decimal]]

    # true regrade transition matrix: P(dest | source, a regrade happens).
    # Each source's destination distribution must sum to 1; a node absent
    # from this mapping never regrades. May include EXTEND_SAME as a
    # destination (see its docstring).
    regrade_transition: Mapping[NodeId, Mapping[NodeId, Decimal]]

    regrade_hazard: HazardFn
    churn_hazard: HazardFn

    # true closure hazards/breakage by (order_channel, txn_type, product)
    # -- already resolved from the continuous cycle-time model (see
    # engine.testing.config) into a discrete per-segment kernel.
    closure: Mapping[tuple[ChannelId, TxnType, str], ClosureProfile]

    # churn's order_channel is a flat relative weight (a channel's weight
    # is ignored before its launch period); regrade's is a period-varying
    # compositional mix (see regrade_order_channel_mix below), since only
    # regrade specifies drift in the source config.
    order_channel_weight: Mapping[ChannelId, Decimal]

    regrade_order_channel_mix: Mapping[Period, Mapping[ChannelId, Decimal]] = field(
        default_factory=dict
    )
    # fraction of node-moving regrades that also reset the contract clock
    # (re-cohort at months_since_contract_start = 0). A regrade landing on
    # EXTEND_SAME always resets it, regardless of this rate.
    regrade_extension_with_move_rate: Decimal = _ZERO

    migrations: tuple[MigrationSchedule, ...] = ()
    pathology_rates: PathologyRates = field(default_factory=PathologyRates)
    self_check: SelfCheckConfig = field(default_factory=SelfCheckConfig)

    initial_base: Mapping[NodeId, Decimal] = field(default_factory=dict)
    channel_launch_period: Mapping[ChannelId, int] = field(default_factory=dict)
    node_launch_period: Mapping[NodeId, int] = field(default_factory=dict)
    node_retire_period: Mapping[NodeId, int] = field(default_factory=dict)
    structural_break: StructuralBreak | None = None

    def __post_init__(self) -> None:
        for source, dist in self.regrade_transition.items():
            total = sum(dist.values(), start=_ZERO)
            if dist and abs(total - _ONE) > Decimal("1e-9"):
                raise ValueError(f"regrade_transition[{source}] must sum to 1, got {total}")
        for channel, term_dist in self.contract_term_mix.items():
            total = sum(term_dist.values(), start=_ZERO)
            if term_dist and abs(total - _ONE) > Decimal("1e-9"):
                raise ValueError(f"contract_term_mix[{channel}] must sum to 1, got {total}")

    def is_channel_launched(self, channel: ChannelId, period: int) -> bool:
        return period >= self.channel_launch_period.get(channel, 0)

    def is_node_active(self, node: NodeId, period: int) -> bool:
        if period < self.node_launch_period.get(node, 0):
            return False
        retire = self.node_retire_period.get(node)
        return retire is None or period < retire


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
    migration_movements: Sequence[BaseMovements] = (),
) -> tuple[BaseSnapshot, ...]:
    """Derive Identity 2 movements from a raw_subscription_event log (one
    row per subscriber-level base-affecting event), plus any migration
    overlay legs (see _migration_movements -- these bypass the order
    pipeline entirely per invariant 8, so they're supplied directly as
    BaseMovements rather than as subscription events), and roll the base
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

    for m in migration_movements:
        movements_by_node_period.setdefault((m.node, int(m.period)), []).append(m)

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
    generate_portfolio itself, since it's the same two builder functions.

    Migrations aren't stored as events on the portfolio (they're a pure
    function of params.migrations, with no randomness involved) -- they're
    recomputed fresh here via _migration_movements rather than carried
    forward from whatever the original generate_portfolio call produced.
    """
    params = portfolio.params
    migration_movements = _migration_movements(params)
    base_nodes = params.product_hierarchy.nodes
    if any(m.to_node is None for m in params.migrations):
        base_nodes = (*base_nodes, OFF_PORTFOLIO_SINK)
    return replace(
        portfolio,
        raw_base_snapshot=build_base_snapshots(
            portfolio.raw_subscription_event,
            nodes=base_nodes,
            horizon=params.horizon,
            initial_base=params.initial_base,
            migration_movements=migration_movements,
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


def _negative_binomial(rng: random.Random, mean: Decimal, dispersion: Decimal) -> int:
    """A Gamma-Poisson mixture: draw a rate from Gamma(shape=1/dispersion,
    scale=mean*dispersion), then Poisson-sample a count from that rate.
    `dispersion` is the negative binomial's overdispersion parameter
    (variance = mean + dispersion*mean^2); dispersion -> 0 recovers plain
    Poisson. stdlib-only (random.gammavariate), matching the rest of this
    module's dependency-free stochastic primitives."""
    if mean <= 0:
        return 0
    if dispersion <= 0:
        return _poisson(rng, mean)
    shape = float(_ONE / dispersion)
    scale = float(mean * dispersion)
    rate = rng.gammavariate(shape, scale)
    return _poisson(rng, Decimal(rate))


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
    contract_term: int
    acquired_period: int
    contract_started_period: int
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
    live subscriber population using per-subscriber tenure and
    months-since-contract-start, so the generated data has genuine
    tenure/contract-expiry-dependent churn/regrade structure for the
    estimators in engine/estimate.py to recover.

    Acquisition is a per-node negative-binomial count process, split
    across channels by the (already period-resolved, drift-included)
    channel_mix, with contract terms assigned per the closing order's
    channel via contract_term_mix. Migrations are applied as a separate,
    order-pipeline-bypassing pass over params.migrations. Pathology rates
    and self_check gates are applied as a post-pass after the base
    generation loop -- see _apply_pathology_rates and _run_self_check.
    """
    rng = random.Random(params.seed)
    nodes = params.product_hierarchy.nodes
    horizon = params.horizon

    order_events: list[OrderEvent] = []
    subscription_events: list[SubscriptionEvent] = []

    # tasks keyed by the period they take effect in
    pending_acquisitions: dict[int, list[tuple[NodeId, ChannelId, int]]] = {}
    pending_regrade_closes: dict[int, list[tuple[str, NodeId, NodeId, bool]]] = {}
    pending_churn_closes: dict[int, list[tuple[str, NodeId]]] = {}

    live: dict[str, _LiveSubscriber] = {}
    next_subscriber_id = 0

    def new_subscriber_id() -> str:
        nonlocal next_subscriber_id
        sid = f"sub-{next_subscriber_id:08d}"
        next_subscriber_id += 1
        return sid

    def closure_profile(channel: ChannelId, txn_type: TxnType, product: str) -> ClosureProfile:
        try:
            return params.closure[(channel, txn_type, product)]
        except KeyError:
            raise KeyError(
                f"no ClosureProfile configured for (order_channel={channel!r}, "
                f"txn_type={txn_type!r}, product={product!r})"
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
        product = params.product_hierarchy.product_of[node]
        profile = closure_profile(channel, txn_type, product)
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

    def sample_churn_order_channel(period: int) -> ChannelId:
        eligible = [
            c
            for c in params.channel_hierarchy.sub_channels
            if params.is_channel_launched(c, period)
            and params.order_channel_weight.get(c, _ZERO) > 0
        ]
        weights = [params.order_channel_weight[c] for c in eligible]
        return ChannelId(_weighted_choice(rng, eligible, weights))

    def sample_regrade_order_channel(period: int) -> ChannelId:
        raw_mix = params.regrade_order_channel_mix.get(Period(period), {})
        launched_mix = {
            c: share
            for c, share in raw_mix.items()
            if params.is_channel_launched(c, period) and share > 0
        }
        mix = launched_mix or raw_mix
        return ChannelId(_weighted_choice(rng, list(mix.keys()), list(mix.values())))

    def sample_contract_term(channel: ChannelId) -> int:
        mix = params.contract_term_mix.get(channel, {})
        terms = list(mix.keys())
        weights = list(mix.values())
        return int(_weighted_choice(rng, [str(term) for term in terms], [w for w in weights]))

    for t in range(horizon):
        # -- acquisitions raised this period: a per-node negative-binomial
        # count, split across channels by the resolved channel_mix --
        acq_multiplier = _ONE
        if params.structural_break and t >= params.structural_break.break_period:
            acq_multiplier = params.structural_break.acquisition_multiplier
        for node in nodes:
            process = params.acquisition.get(node)
            if process is None:
                continue
            mean = process.mean_by_period.get(Period(t), _ZERO) * acq_multiplier
            total = _negative_binomial(rng, mean, process.dispersion)
            if total <= 0:
                continue
            raw_mix = params.channel_mix.get(node, {}).get(Period(t), {})
            launched_mix = {
                c: share
                for c, share in raw_mix.items()
                if params.is_channel_launched(c, t) and share > 0
            }
            if not launched_mix:
                continue
            launched_total = sum(launched_mix.values(), start=_ZERO)
            mix = {c: share / launched_total for c, share in launched_mix.items()}
            channels = list(mix.keys())
            shares = list(mix.values())
            counts = _multinomial_split(
                rng, total, [*shares[:-1], _ONE - sum(shares[:-1], start=_ZERO)]
            )
            for channel, n in zip(channels, counts, strict=True):
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
            ctx = HazardContext(
                period=Period(t),
                tenure=t - rec.acquired_period,
                months_since_contract_start=t - rec.contract_started_period,
                contract_term=rec.contract_term,
                acquisition_channel=rec.acquisition_channel,
                node=rec.node,
            )
            p_churn = min(float(params.churn_hazard(ctx) * churn_multiplier), 1.0)
            p_regrade = min(float(params.regrade_hazard(ctx)), 1.0)
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
            channel = sample_churn_order_channel(t)
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
            ) -> None:
                batch, sids[:count] = sids[:count], []
                for sid in batch:
                    pending_churn_closes.setdefault(resolve_period, []).append((sid, node))

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

        regrade_groups: dict[tuple[NodeId, ChannelId], list[tuple[str, NodeId, bool]]] = {}
        for sid in regrade_candidates:
            rec = live[sid]
            dest_dist = params.regrade_transition.get(rec.node, {})
            if not dest_dist:
                continue
            rec.open_regrade = True
            drawn = NodeId(_weighted_choice(rng, list(dest_dist.keys()), list(dest_dist.values())))
            if drawn == EXTEND_SAME:
                dest, resets_contract = rec.node, True
            else:
                dest = drawn
                resets_contract = rng.random() < float(params.regrade_extension_with_move_rate)
            channel = sample_regrade_order_channel(t)
            regrade_groups.setdefault((rec.node, channel), []).append((sid, dest, resets_contract))

        for (node, channel), triples in regrade_groups.items():
            order_events.append(
                OrderEvent(
                    node=node,
                    order_channel=channel,
                    txn_type=TxnType.REGRADE,
                    raise_period=Period(t),
                    event_type="raised",
                    event_period=Period(t),
                    count=Decimal(len(triples)),
                )
            )
            rng.shuffle(triples)

            def on_close_regrade(
                resolve_period: int,
                count: int,
                triples: list[tuple[str, NodeId, bool]] = triples,
                node: NodeId = node,
            ) -> None:
                batch, triples[:count] = triples[:count], []
                for sid, dest, resets_contract in batch:
                    pending_regrade_closes.setdefault(resolve_period, []).append(
                        (sid, node, dest, resets_contract)
                    )

            def on_break_regrade(
                count: int, triples: list[tuple[str, NodeId, bool]] = triples
            ) -> None:
                batch, triples[:count] = triples[:count], []
                for sid, _dest, _resets in batch:
                    live[sid].open_regrade = False

            schedule_resolution(
                node,
                channel,
                TxnType.REGRADE,
                t,
                len(triples),
                on_close=on_close_regrade,
                on_break=on_break_regrade,
            )

        # -- apply this period's resolutions to the live population / base --
        for node, channel, count in pending_acquisitions.pop(t, []):
            for _ in range(count):
                sid = new_subscriber_id()
                term = sample_contract_term(channel)
                live[sid] = _LiveSubscriber(
                    node=node,
                    acquisition_channel=channel,
                    contract_term=term,
                    acquired_period=t,
                    contract_started_period=t,
                )
                subscription_events.append(
                    SubscriptionEvent(
                        subscriber_id=sid,
                        event_type="acquired",
                        period=Period(t),
                        node=node,
                        from_node=None,
                        acquisition_channel=channel,
                        contract_term=str(term),
                        tenure=0,
                    )
                )

        for sid, from_node, dest, resets_contract in pending_regrade_closes.pop(t, []):
            resolving = live.get(sid)
            if resolving is None:
                continue  # churned before this regrade closed -- see pathology docs
            resolving.node = dest
            resolving.open_regrade = False
            if resets_contract:
                resolving.contract_started_period = t
            subscription_events.append(
                SubscriptionEvent(
                    subscriber_id=sid,
                    event_type="regraded",
                    period=Period(t),
                    node=dest,
                    from_node=from_node,
                    acquisition_channel=resolving.acquisition_channel,
                    contract_term=str(resolving.contract_term),
                    tenure=t - resolving.acquired_period,
                )
            )

        for sid, node in pending_churn_closes.pop(t, []):
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
                    contract_term=str(resolved.contract_term),
                    tenure=t - resolved.acquired_period,
                )
            )

    migration_movements = _migration_movements(params)
    base_nodes = nodes
    if any(m.to_node is None for m in params.migrations):
        base_nodes = (*nodes, OFF_PORTFOLIO_SINK)

    base_snapshots = build_base_snapshots(
        subscription_events,
        nodes=base_nodes,
        horizon=horizon,
        initial_base=params.initial_base,
        migration_movements=migration_movements,
    )
    order_book_snapshots = build_order_book_snapshots(
        order_events, nodes=nodes, channels=params.channel_hierarchy.sub_channels, horizon=horizon
    )

    portfolio = SyntheticPortfolio(
        params=params,
        raw_order_event=tuple(order_events),
        raw_subscription_event=tuple(subscription_events),
        raw_base_snapshot=base_snapshots,
        raw_order_book_snapshot=order_book_snapshots,
    )
    portfolio = _apply_pathology_rates(portfolio, rng)
    _run_self_check(portfolio)
    return portfolio


def _migration_movements(params: SyntheticParams) -> tuple[BaseMovements, ...]:
    """Expand every MigrationSchedule into paired BaseMovements legs via
    engine.base.MigrationPair/migration_pair_movements -- migrations bypass
    the order pipeline entirely (invariant 8) and are applied independently
    of the main period loop above."""
    movements: list[BaseMovements] = []
    for schedule in params.migrations:
        for period, count in schedule.schedule.items():
            if count <= 0:
                continue
            pair = MigrationPair(
                from_node=schedule.from_node,
                to_node=schedule.resolved_to_node,
                period=period,
                count=count,
            )
            source_leg, dest_leg = migration_pair_movements(pair)
            movements.append(source_leg)
            movements.append(dest_leg)
    return tuple(movements)


def _apply_pathology_rates(portfolio: SyntheticPortfolio, rng: random.Random) -> SyntheticPortfolio:
    """Apply params.pathology_rates' continuous rate knobs, reusing the
    one-off injectors in engine/testing/pathologies.py under the hood. See
    that module's rate-driven appliers for what each knob does.

    Imports pathologies lazily (inside this function, not at module top
    level) to avoid a circular import: pathologies.py imports the
    dataclasses/builders in *this* module."""
    from engine.testing.pathologies import apply_pathology_rates

    return apply_pathology_rates(portfolio, rng)


def _run_self_check(portfolio: SyntheticPortfolio) -> None:
    """Verify the invariants params.self_check has enabled, raising
    AssertionError on the first violation found. These should always hold
    by construction (see the module docstring); this is defense in depth,
    matching the spec's output.self_check gates: "sanity gates the
    simulator must pass before writing anything -- if the generator can't
    produce data satisfying the invariants, no test built on it means
    anything."
    """
    cfg = portfolio.params.self_check

    if cfg.assert_kernel_sums_to_one:
        for key, profile in portfolio.params.closure.items():
            total = sum(profile.g, start=_ZERO) + profile.breakage
            if abs(total - _ONE) > Decimal("1e-9"):
                raise AssertionError(f"closure kernel {key} sums to {total}, not 1")

    if cfg.assert_order_book_identity:
        for book_row in portfolio.raw_order_book_snapshot:
            if book_row.open_orders < _ZERO:
                raise AssertionError(
                    f"negative open_orders at {book_row.node}/{book_row.order_channel}/"
                    f"{book_row.txn_type}/{book_row.period}: {book_row.open_orders}"
                )

    if cfg.assert_base_identity:
        by_node: dict[NodeId, list[BaseSnapshot]] = {}
        for base_row in portfolio.raw_base_snapshot:
            by_node.setdefault(base_row.node, []).append(base_row)
        for node, rows in by_node.items():
            rows.sort(key=lambda r: int(r.period))
            prev_closing = portfolio.params.initial_base.get(node, _ZERO)
            for row in rows:
                if row.opening_base != prev_closing:
                    raise AssertionError(
                        f"base identity broken at {node}/{row.period}: opening "
                        f"{row.opening_base} != prior closing {prev_closing}"
                    )
                expected_closing = (
                    row.opening_base
                    + row.closed_acquisition
                    + row.closed_resign_to
                    - row.closed_resign_from
                    - row.churn
                    + row.migration_acq
                    - row.migration_churn
                )
                if row.closing_base != expected_closing:
                    raise AssertionError(
                        f"base identity broken at {node}/{row.period}: closing "
                        f"{row.closing_base} != expected {expected_closing}"
                    )
                prev_closing = row.closing_base

    if cfg.assert_non_negative_base:
        for base_row in portfolio.raw_base_snapshot:
            if base_row.closing_base < _ZERO:
                raise AssertionError(
                    f"negative base at {base_row.node}/{base_row.period}: {base_row.closing_base}"
                )

    if cfg.assert_resign_conservation:
        by_period_to: dict[Period, Decimal] = {}
        by_period_from: dict[Period, Decimal] = {}
        for base_row in portfolio.raw_base_snapshot:
            by_period_to[base_row.period] = (
                by_period_to.get(base_row.period, _ZERO) + base_row.closed_resign_to
            )
            by_period_from[base_row.period] = (
                by_period_from.get(base_row.period, _ZERO) + base_row.closed_resign_from
            )
        for period, to_total in by_period_to.items():
            from_total = by_period_from.get(period, _ZERO)
            if to_total != from_total:
                raise AssertionError(
                    f"resign conservation broken at {period}: to={to_total} from={from_total}"
                )

    if cfg.assert_migrations_net_zero_when_paired:
        by_period_acq: dict[Period, Decimal] = {}
        by_period_churn: dict[Period, Decimal] = {}
        for base_row in portfolio.raw_base_snapshot:
            by_period_acq[base_row.period] = (
                by_period_acq.get(base_row.period, _ZERO) + base_row.migration_acq
            )
            by_period_churn[base_row.period] = (
                by_period_churn.get(base_row.period, _ZERO) + base_row.migration_churn
            )
        for period, acq_total in by_period_acq.items():
            churn_total = by_period_churn.get(period, _ZERO)
            if acq_total != churn_total:
                raise AssertionError(
                    f"migrations don't net to zero at {period}: acq={acq_total} churn={churn_total}"
                )


if __name__ == "__main__":
    # `python -m engine.testing.synthetic --base <config.yaml> --out <dir>`
    # -- see engine/testing/cli.py. Imported lazily, here, rather than at
    # module level: engine.testing.cli pulls in PyYAML, which nothing else
    # in this module needs, so `import engine.testing.synthetic` alone
    # (what every other caller in this package does) never requires it.
    from engine.testing.cli import main

    main()
