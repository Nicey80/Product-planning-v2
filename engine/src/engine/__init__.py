"""Standalone subscription base and movements forecasting library.

Import from here for the stable public surface; see CLAUDE.md and
docs/domain-model.md for the domain contract this package implements.
"""

from engine.base import (
    BaseMovements,
    MigrationPair,
    RegradePair,
    merge_movements,
    migration_pair_movements,
    regrade_pair_movements,
    roll_forward_base,
)
from engine.domain import ChannelId, KernelSegment, NodeId, Period, TxnType
from engine.estimate import (
    OrderObservation,
    estimate_churn_hazard,
    estimate_closure_kernel,
    estimate_regrade_transition,
    naive_kernel_estimate,
    observations_from_order_events,
    raised_by_cohort_from_events,
)
from engine.hierarchy import cross_margin_total, roll_up, sum_axis
from engine.kernel import ClosureResult, apply_closure_kernel
from engine.run import ForecastRun
from engine.simulate import simulate_node_base

__all__ = [
    "BaseMovements",
    "ChannelId",
    "ClosureResult",
    "ForecastRun",
    "KernelSegment",
    "MigrationPair",
    "NodeId",
    "OrderObservation",
    "Period",
    "RegradePair",
    "TxnType",
    "apply_closure_kernel",
    "cross_margin_total",
    "estimate_churn_hazard",
    "estimate_closure_kernel",
    "estimate_regrade_transition",
    "merge_movements",
    "migration_pair_movements",
    "naive_kernel_estimate",
    "observations_from_order_events",
    "raised_by_cohort_from_events",
    "regrade_pair_movements",
    "roll_forward_base",
    "roll_up",
    "simulate_node_base",
    "sum_axis",
]
