"""Base roll-forward (Identity 2), regrade pairing, and migration overlays.

    closing_base[t] = opening_base[t]
                     + closed_acquisition[t] + closed_resign_to[t]
                     - closed_resign_from[t] - churn[t]
                     + migration_acq[t] - migration_churn[t]
    opening_base[t] = closing_base[t-1]

BaseMovements deliberately has no field for raised/open_orders values
(invariant 7: raised volumes never directly affect the base) and no
order_channel field on migration movements (invariant 8: migrations
bypass the order pipeline entirely).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal

from engine.domain import NodeId, Period

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class BaseMovements:
    """Base-affecting movements for one node in one period."""

    node: NodeId
    period: Period
    closed_acquisition: Decimal = _ZERO
    closed_resign_to: Decimal = _ZERO
    closed_resign_from: Decimal = _ZERO
    churn: Decimal = _ZERO
    migration_acq: Decimal = _ZERO
    migration_churn: Decimal = _ZERO


# Movements never carry a "raised" or "open_orders" component — structural
# guarantee backing invariant 7, checked in tests/test_invariants.py.
BASE_MOVEMENT_FIELDS: frozenset[str] = frozenset(f.name for f in fields(BaseMovements))


def roll_forward_base(opening_base: Decimal, movements: BaseMovements) -> Decimal:
    """Apply Identity 2 for a single node/period."""
    return (
        opening_base
        + movements.closed_acquisition
        + movements.closed_resign_to
        - movements.closed_resign_from
        - movements.churn
        + movements.migration_acq
        - movements.migration_churn
    )


@dataclass(frozen=True, slots=True)
class RegradePair:
    """A closed regrade order: subscribers move from `from_node` to
    `to_node` in `period`, producing a linked resign_from/resign_to pair
    (invariant 3). A pure contract extension uses from_node == to_node."""

    from_node: NodeId
    to_node: NodeId
    period: Period
    count: Decimal

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("regrade count must be >= 0")


def regrade_pair_movements(pair: RegradePair) -> tuple[BaseMovements, BaseMovements]:
    """Return (source_leg, destination_leg) movements for a closed regrade.

    The two legs always carry the same count, in the same period, so
    summing closed_resign_to across nodes always equals summing
    closed_resign_from across nodes for that period (invariant 3).
    """
    source = BaseMovements(node=pair.from_node, period=pair.period, closed_resign_from=pair.count)
    destination = BaseMovements(node=pair.to_node, period=pair.period, closed_resign_to=pair.count)
    return source, destination


@dataclass(frozen=True, slots=True)
class MigrationPair:
    """A portfolio-internal forced move between nodes, overlay-driven and
    never routed through the order pipeline (invariant 8)."""

    from_node: NodeId
    to_node: NodeId
    period: Period
    count: Decimal

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("migration count must be >= 0")


def migration_pair_movements(pair: MigrationPair) -> tuple[BaseMovements, BaseMovements]:
    """Return (source_leg, destination_leg) movements for a migration.

    Always paired and net to zero across the two nodes: the destination's
    migration_acq exactly equals the source's migration_churn.
    """
    source = BaseMovements(node=pair.from_node, period=pair.period, migration_churn=pair.count)
    destination = BaseMovements(node=pair.to_node, period=pair.period, migration_acq=pair.count)
    return source, destination


def merge_movements(node: NodeId, period: Period, movements: list[BaseMovements]) -> BaseMovements:
    """Combine multiple movement rows for the same node/period (e.g. several
    regrade legs landing on the same node) into one BaseMovements."""
    totals = dict.fromkeys(
        (
            "closed_acquisition",
            "closed_resign_to",
            "closed_resign_from",
            "churn",
            "migration_acq",
            "migration_churn",
        ),
        _ZERO,
    )
    for m in movements:
        if m.node != node or m.period != period:
            raise ValueError("all movements must share the given node and period")
        for key in totals:
            totals[key] += getattr(m, key)
    return BaseMovements(node=node, period=period, **totals)
