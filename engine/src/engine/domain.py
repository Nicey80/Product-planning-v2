"""Core domain types.

Terminology matches CLAUDE.md and docs/domain-model.md exactly. Types are
frozen/immutable so that engine computations stay pure and forecast runs
(see run.py) can be immutable by construction (invariant 9).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import NewType

Period = NewType("Period", int)
"""A discrete, ordered period key (e.g. a week or month index). Never a
wall-clock timestamp — see CLAUDE.md conventions."""

NodeId = NewType("NodeId", str)
"""Leaf product sub-variant identifier."""

ChannelId = NewType("ChannelId", str)
"""Leaf selling-channel (sub_channel/partner) identifier. Used for both
order_channel and acquisition_channel values."""

_TOLERANCE = Decimal("1e-9")


class TxnType(StrEnum):
    """Order transaction type. Migration moves are deliberately absent —
    they bypass the order pipeline entirely (invariant 8)."""

    ACQUISITION = "acquisition"
    REGRADE = "regrade"
    CHURN = "churn"


@dataclass(frozen=True, slots=True)
class KernelSegment:
    """Closure kernel g(k) for one node x order_channel x txn_type segment.

    g[k] is the probability that an order raised in period s closes in
    period s + k. breakage is the residual probability that the order
    never closes. Contract (invariant 2): sum(g) + breakage == 1 exactly
    (within floating tolerance), enforced at construction.
    """

    node: NodeId
    order_channel: ChannelId
    txn_type: TxnType
    g: tuple[Decimal, ...]
    breakage: Decimal

    def __post_init__(self) -> None:
        if self.breakage < 0:
            raise ValueError("breakage must be >= 0")
        if any(p < 0 for p in self.g):
            raise ValueError("all g(k) values must be >= 0")
        total = sum(self.g, start=Decimal(0)) + self.breakage
        if abs(total - Decimal(1)) > _TOLERANCE:
            raise ValueError(f"sum(g) + breakage must equal 1, got {total}")

    @property
    def max_lag(self) -> int:
        """Number of periods after which a raise cohort is fully resolved
        (closed or broken) — i.e. len(g)."""
        return len(self.g)
