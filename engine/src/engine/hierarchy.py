"""Hierarchy roll-up and cross-margin reconciliation (invariant 6).

Both the product hierarchy (variant -> product -> product_group) and the
channel hierarchy (sub_channel -> channel -> channel_group) are summed
the same way: parent values are *always* computed by summing the same
underlying leaf cells, never forecast independently, so the identity
holds exactly rather than approximately.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

_ZERO = Decimal(0)


def roll_up(leaf_values: Mapping[str, Decimal], parent_of: Mapping[str, str]) -> dict[str, Decimal]:
    """Sum leaf-level values up one hierarchy level.

    `leaf_values` is keyed by leaf id; `parent_of` maps leaf id -> parent
    id. Every key in `leaf_values` must have an entry in `parent_of`.
    """
    totals: dict[str, Decimal] = {}
    for leaf, value in leaf_values.items():
        parent = parent_of[leaf]
        totals[parent] = totals.get(parent, _ZERO) + value
    return totals


def cross_margin_total(cells: Mapping[tuple[str, str], Decimal]) -> Decimal:
    """Grand total over a node x channel cell mapping.

    Summing rows-then-columns or columns-then-rows both reduce to this
    same grand total — the point of invariant 6's cross-margin check.
    """
    return sum(cells.values(), start=_ZERO)


def sum_axis(cells: Mapping[tuple[str, str], Decimal], *, fixed_index: int) -> dict[str, Decimal]:
    """Collapse a node x channel cell mapping along one axis.

    fixed_index=0 keeps the node key (summing over channel), fixed_index=1
    keeps the channel key (summing over node).
    """
    totals: dict[str, Decimal] = {}
    for key, value in cells.items():
        kept = key[fixed_index]
        totals[kept] = totals.get(kept, _ZERO) + value
    return totals
