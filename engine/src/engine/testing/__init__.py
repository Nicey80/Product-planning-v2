"""Synthetic data generation for testing engine/ against known parameters.

Not part of engine's production surface (backend/frontend must never import
`engine.testing`) -- see synthetic.py for the portfolio simulator and
engine/estimate.py for the estimators it exists to validate.
"""

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
    ChannelHierarchy,
    ClosureProfile,
    OrderBookSnapshot,
    OrderEvent,
    ProductHierarchy,
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

__all__ = [
    "ChannelHierarchy",
    "ClosureProfile",
    "OrderBookSnapshot",
    "OrderEvent",
    "ProductHierarchy",
    "StructuralBreak",
    "SubscriptionEvent",
    "SyntheticParams",
    "SyntheticPortfolio",
    "build_base_snapshots",
    "build_order_book_snapshots",
    "censored_raise_periods",
    "generate_portfolio",
    "make_channel_hierarchy",
    "make_product_hierarchy",
    "observation_cutoff",
    "pathology_backdated_event",
    "pathology_bulk_closure_batch",
    "pathology_channel_launched_mid_history",
    "pathology_churn_with_open_regrade",
    "pathology_mid_history_structural_break",
    "pathology_orphaned_orders",
    "pathology_right_censored_recent_cohorts",
    "pathology_thin_volume_node",
    "rebuild_snapshots",
]
