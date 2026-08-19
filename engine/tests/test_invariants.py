"""Property-based tests for the nine domain invariants (see CLAUDE.md).

These are the contract for everything downstream in engine/. They were
written against the domain model before the forecasting/API layers exist,
and every later change to engine/ must keep them green.
"""

from __future__ import annotations

import dataclasses
from decimal import ROUND_DOWN, Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.base import (
    BASE_MOVEMENT_FIELDS,
    BaseMovements,
    MigrationPair,
    RegradePair,
    migration_pair_movements,
    regrade_pair_movements,
    roll_forward_base,
)
from engine.domain import ChannelId, KernelSegment, NodeId, Period, TxnType
from engine.hierarchy import cross_margin_total, roll_up, sum_axis
from engine.kernel import apply_closure_kernel
from engine.run import ForecastRun
from engine.simulate import simulate_node_base

_QUANTUM = Decimal("0.000001")
_ZERO = Decimal(0)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

nonneg_decimal = st.decimals(
    min_value=0, max_value=1000, places=2, allow_nan=False, allow_infinity=False
)


@st.composite
def kernel_segments(
    draw: st.DrawFn,
    node: str = "n1",
    order_channel: str = "c1",
    txn_type: TxnType = TxnType.ACQUISITION,
    max_lag: int = 5,
) -> KernelSegment:
    """A KernelSegment whose g(k) + breakage sums to exactly 1.

    Weights are rounded down to a fixed precision so the running total
    never exceeds the true (<=1) fractional total, guaranteeing a
    non-negative breakage residual by construction.
    """
    n = draw(st.integers(min_value=1, max_value=max_lag))
    weights = draw(st.lists(st.integers(min_value=0, max_value=25), min_size=n, max_size=n))
    total = sum(weights)
    if total == 0:
        weights = [1] + weights[1:]
        total = sum(weights)

    g_list: list[Decimal] = []
    running = Decimal(0)
    for w in weights:
        frac = (Decimal(w) / Decimal(total)).quantize(_QUANTUM, rounding=ROUND_DOWN)
        g_list.append(frac)
        running += frac
    # breakage is whatever fraction is left, so g + breakage sums to
    # exactly 1 -- not just approximately, since running is exact.
    breakage = Decimal(1) - running

    return KernelSegment(
        node=NodeId(node),
        order_channel=ChannelId(order_channel),
        txn_type=txn_type,
        g=tuple(g_list),
        breakage=breakage,
    )


raise_series = st.dictionaries(
    keys=st.integers(min_value=0, max_value=20).map(Period),
    values=nonneg_decimal,
    min_size=1,
    max_size=8,
)


# ---------------------------------------------------------------------------
# Invariant 1: open_orders[t] = open_orders[t-1] + raised[t] - closed[t] - broken[t]
# ---------------------------------------------------------------------------


@given(raised=raise_series, kernel=kernel_segments())
@settings(max_examples=200)
def test_identity1_order_book_roll_forward(
    raised: dict[Period, Decimal], kernel: KernelSegment
) -> None:
    result = apply_closure_kernel(raised, kernel)
    periods = result.periods()
    prev_open = _ZERO
    for t in periods:
        expected = prev_open + raised.get(t, _ZERO) - result.closed[t] - result.broken[t]
        assert result.open_orders[t] == expected
        prev_open = result.open_orders[t]


# ---------------------------------------------------------------------------
# Invariant 2: sum(g) + breakage == 1 for every kernel segment
# ---------------------------------------------------------------------------


@given(kernel=kernel_segments())
@settings(max_examples=200)
def test_identity2_kernel_sums_to_one(kernel: KernelSegment) -> None:
    total = sum(kernel.g, start=Decimal(0)) + kernel.breakage
    assert total == Decimal(1)


def test_kernel_construction_rejects_bad_sum() -> None:
    with pytest.raises(ValueError):
        KernelSegment(
            node=NodeId("n1"),
            order_channel=ChannelId("c1"),
            txn_type=TxnType.ACQUISITION,
            g=(Decimal("0.5"), Decimal("0.3")),
            breakage=Decimal("0.5"),  # 0.5 + 0.3 + 0.5 = 1.3 != 1
        )


# ---------------------------------------------------------------------------
# Invariant 3: sum(closed_resign_to) == sum(closed_resign_from) across nodes,
# per period.
# ---------------------------------------------------------------------------

node_ids = st.text(alphabet="ABCDE", min_size=1, max_size=1).map(NodeId)


@given(
    period=st.integers(min_value=0, max_value=10).map(Period),
    pairs=st.lists(
        st.tuples(node_ids, node_ids, nonneg_decimal),
        min_size=0,
        max_size=10,
    ),
)
@settings(max_examples=200)
def test_regrade_resign_to_equals_resign_from(
    period: Period, pairs: list[tuple[NodeId, NodeId, Decimal]]
) -> None:
    regrades = [RegradePair(from_node=f, to_node=t, period=period, count=c) for f, t, c in pairs]
    total_to = _ZERO
    total_from = _ZERO
    for pair in regrades:
        source_leg, dest_leg = regrade_pair_movements(pair)
        total_from += source_leg.closed_resign_from
        total_to += dest_leg.closed_resign_to
        assert source_leg.closed_resign_from == dest_leg.closed_resign_to == pair.count

    assert total_to == total_from


# ---------------------------------------------------------------------------
# Invariant 4: cumulative closed + cumulative broken + open <= cumulative
# raised, per node x channel (checked here per kernel segment).
# ---------------------------------------------------------------------------


@given(raised=raise_series, kernel=kernel_segments())
@settings(max_examples=200)
def test_identity4_resolutions_never_exceed_raised(
    raised: dict[Period, Decimal], kernel: KernelSegment
) -> None:
    result = apply_closure_kernel(raised, kernel)
    periods = result.periods()
    cum_raised = _ZERO
    cum_closed = _ZERO
    cum_broken = _ZERO
    for t in periods:
        cum_raised += raised.get(t, _ZERO)
        cum_closed += result.closed[t]
        cum_broken += result.broken[t]
        assert cum_closed + cum_broken + result.open_orders[t] <= cum_raised + _QUANTUM

    # By the last period (first raise + kernel horizon), every raised order
    # has resolved to closed or broken and the book is empty.
    last_t = periods[-1]
    assert result.open_orders[last_t] == _ZERO
    assert cum_closed + cum_broken == cum_raised


# ---------------------------------------------------------------------------
# Invariant 5: open_orders >= 0 and base >= 0 at every node and period.
# ---------------------------------------------------------------------------


@given(raised=raise_series, kernel=kernel_segments())
@settings(max_examples=200)
def test_identity5_open_orders_nonnegative(
    raised: dict[Period, Decimal], kernel: KernelSegment
) -> None:
    result = apply_closure_kernel(raised, kernel)
    for value in result.open_orders.values():
        assert value >= _ZERO


rate = st.decimals(min_value=0, max_value=1, places=4, allow_nan=False, allow_infinity=False)


@given(
    opening_base=nonneg_decimal,
    acquisitions=st.lists(nonneg_decimal, min_size=6, max_size=6),
    resign_to=st.lists(nonneg_decimal, min_size=6, max_size=6),
    resign_from_rates=st.lists(rate, min_size=6, max_size=6),
    churn_rates=st.lists(rate, min_size=6, max_size=6),
    migration_churn_rates=st.lists(rate, min_size=6, max_size=6),
)
@settings(max_examples=200)
def test_identity5_base_nonnegative(
    opening_base: Decimal,
    acquisitions: list[Decimal],
    resign_to: list[Decimal],
    resign_from_rates: list[Decimal],
    churn_rates: list[Decimal],
    migration_churn_rates: list[Decimal],
) -> None:
    periods = [Period(i) for i in range(6)]
    node = NodeId("n1")
    closing = simulate_node_base(
        node=node,
        opening_base=opening_base,
        periods=periods,
        closed_acquisition=dict(zip(periods, acquisitions, strict=True)),
        closed_resign_to=dict(zip(periods, resign_to, strict=True)),
        resign_from_rate=dict(zip(periods, resign_from_rates, strict=True)),
        churn_rate=dict(zip(periods, churn_rates, strict=True)),
        migration_churn_rate=dict(zip(periods, migration_churn_rates, strict=True)),
    )
    for value in closing.values():
        assert value >= _ZERO


# ---------------------------------------------------------------------------
# Invariant 6: leaf forecasts sum exactly to parents on both hierarchies,
# and to their cross-margins.
# ---------------------------------------------------------------------------

leaf_ids = st.text(alphabet="abcdefgh", min_size=1, max_size=1)


@given(leaf_values=st.dictionaries(leaf_ids, nonneg_decimal, min_size=1, max_size=8))
@settings(max_examples=200)
def test_identity6_hierarchy_rollup(leaf_values: dict[str, Decimal]) -> None:
    # Deterministic leaf -> parent assignment (3 parents) independent of any
    # separately-drawn strategy, so it always covers every generated leaf.
    parent_of = {leaf: f"P{ord(leaf) % 3}" for leaf in leaf_values}
    parents = roll_up(leaf_values, parent_of)

    for parent in {parent_of[leaf] for leaf in leaf_values}:
        expected = sum(
            (v for leaf, v in leaf_values.items() if parent_of[leaf] == parent),
            start=_ZERO,
        )
        assert parents[parent] == expected

    assert sum(parents.values(), start=_ZERO) == sum(leaf_values.values(), start=_ZERO)


channel_ids = st.text(alphabet="xyz", min_size=1, max_size=1)


@given(
    cells=st.dictionaries(st.tuples(leaf_ids, channel_ids), nonneg_decimal, min_size=1, max_size=15)
)
@settings(max_examples=200)
def test_identity6_cross_margin_reconciles(cells: dict[tuple[str, str], Decimal]) -> None:
    grand_total = cross_margin_total(cells)
    by_node = sum_axis(cells, fixed_index=0)
    by_channel = sum_axis(cells, fixed_index=1)

    assert sum(by_node.values(), start=_ZERO) == grand_total
    assert sum(by_channel.values(), start=_ZERO) == grand_total


# ---------------------------------------------------------------------------
# Invariant 7: raised volumes never directly affect the base -- only
# closures do. BaseMovements has no field through which a raised (or open
# order) count could reach the base roll-forward.
# ---------------------------------------------------------------------------


def test_identity7_base_movements_has_no_raised_field() -> None:
    assert BASE_MOVEMENT_FIELDS.isdisjoint({"raised", "open_orders", "broken"})
    assert {
        "node",
        "period",
        "closed_acquisition",
        "closed_resign_to",
        "closed_resign_from",
        "churn",
        "migration_acq",
        "migration_churn",
    } == BASE_MOVEMENT_FIELDS


@given(
    opening_base=nonneg_decimal,
    movements_kwargs=st.fixed_dictionaries(
        {
            "closed_acquisition": nonneg_decimal,
            "closed_resign_to": nonneg_decimal,
            "closed_resign_from": nonneg_decimal,
            "churn": nonneg_decimal,
            "migration_acq": nonneg_decimal,
            "migration_churn": nonneg_decimal,
        }
    ),
    unrelated_raised=raise_series,
    kernel=kernel_segments(),
)
@settings(max_examples=100)
def test_identity7_base_unaffected_by_unrelated_raised(
    opening_base: Decimal,
    movements_kwargs: dict[str, Decimal],
    unrelated_raised: dict[Period, Decimal],
    kernel: KernelSegment,
) -> None:
    # Computing an (unrelated) closure result and varying its raised input
    # must not change a base roll-forward driven by explicit movements.
    apply_closure_kernel(unrelated_raised, kernel)
    movements = BaseMovements(node=NodeId("n1"), period=Period(0), **movements_kwargs)
    base_a = roll_forward_base(opening_base, movements)

    bigger_raised = {t: v + Decimal(1000) for t, v in unrelated_raised.items()}
    apply_closure_kernel(bigger_raised, kernel)
    base_b = roll_forward_base(opening_base, movements)

    assert base_a == base_b


# ---------------------------------------------------------------------------
# Invariant 8: migration overlays are paired, net to zero, and never pass
# through the order pipeline.
# ---------------------------------------------------------------------------

migration_pipeline_fields = frozenset({"order_channel", "txn_type", "raised", "closed", "broken"})


def test_identity8_migration_pair_has_no_order_pipeline_fields() -> None:
    field_names = {f.name for f in dataclasses.fields(MigrationPair)}
    assert field_names.isdisjoint(migration_pipeline_fields)


@given(
    from_node=node_ids,
    to_node=node_ids,
    period=st.integers(min_value=0, max_value=10).map(Period),
    count=nonneg_decimal,
)
@settings(max_examples=200)
def test_identity8_migration_pair_nets_to_zero(
    from_node: NodeId, to_node: NodeId, period: Period, count: Decimal
) -> None:
    pair = MigrationPair(from_node=from_node, to_node=to_node, period=period, count=count)
    source_leg, dest_leg = migration_pair_movements(pair)

    assert source_leg.migration_churn == dest_leg.migration_acq == count
    # net effect across the two legs on total base is exactly zero
    net = (
        dest_leg.migration_acq
        - source_leg.migration_churn
        + dest_leg.closed_acquisition
        - source_leg.closed_resign_from
        + dest_leg.closed_resign_to
        - dest_leg.churn
        - source_leg.churn
    )
    assert net == _ZERO
    # neither leg touches order-pipeline-derived base fields
    for leg in (source_leg, dest_leg):
        assert leg.closed_acquisition == _ZERO
        assert leg.closed_resign_to == _ZERO
        assert leg.closed_resign_from == _ZERO
        assert leg.churn == _ZERO


# ---------------------------------------------------------------------------
# Invariant 9: forecast runs are immutable once created.
# ---------------------------------------------------------------------------


def test_identity9_forecast_run_is_frozen() -> None:
    run = ForecastRun(
        run_id="run-1",
        created_at="2026-01-01T00:00:00Z",
        inputs={"kernel_version": "v1"},
        outputs={"total_base": Decimal(100)},
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        run.run_id = "run-2"  # type: ignore[misc]

    with pytest.raises(TypeError):
        run.inputs["kernel_version"] = "v2"  # type: ignore[index]

    with pytest.raises(TypeError):
        run.outputs["total_base"] = Decimal(0)  # type: ignore[index]
