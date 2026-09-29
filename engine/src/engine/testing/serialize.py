"""JSON-safe (de)serialization for SyntheticPortfolio.

Single source of truth for "what a raw_* fixture file looks like on
disk" -- shared by the `python -m engine.testing.synthetic` CLI
(engine/testing/cli.py) and the golden fixture harness
(engine/tests/golden_synthetic_scenario.py), so both go through exactly
one code path rather than two independently-maintained copies that could
silently drift apart.

Decimal/enum values are serialized as plain strings (round-trippable via
Decimal()/TxnType()) so the files are diff-friendly JSON, not
Python-repr-dependent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from engine.domain import ChannelId, NodeId, Period, TxnType
from engine.testing.synthetic import (
    BaseSnapshot,
    OrderBookSnapshot,
    OrderEvent,
    SubscriptionEvent,
    SyntheticParams,
    SyntheticPortfolio,
)

_TABLE_NAMES = (
    "raw_order_event",
    "raw_subscription_event",
    "raw_base_snapshot",
    "raw_order_book_snapshot",
)


def serialize_portfolio(portfolio: SyntheticPortfolio) -> dict[str, list[dict[str, Any]]]:
    """The four raw_* tables as JSON-safe dicts, keyed by table name."""
    return {
        "raw_order_event": [_serialize_order_event(e) for e in portfolio.raw_order_event],
        "raw_subscription_event": [
            _serialize_subscription_event(e) for e in portfolio.raw_subscription_event
        ],
        "raw_base_snapshot": [_serialize_base_snapshot(r) for r in portfolio.raw_base_snapshot],
        "raw_order_book_snapshot": [
            _serialize_order_book_snapshot(r) for r in portfolio.raw_order_book_snapshot
        ],
    }


def deserialize_portfolio(
    tables: dict[str, list[dict[str, Any]]], *, params: SyntheticParams
) -> SyntheticPortfolio:
    """Inverse of serialize_portfolio. `params` is not recoverable from the
    JSON (SyntheticParams carries Python callables for the hazard
    functions), so the caller supplies whatever params it already has --
    typically the same ones the portfolio was generated from."""
    missing = [name for name in _TABLE_NAMES if name not in tables]
    if missing:
        raise ValueError(f"missing table(s) in portfolio data: {missing}")
    return SyntheticPortfolio(
        params=params,
        raw_order_event=tuple(_deserialize_order_event(row) for row in tables["raw_order_event"]),
        raw_subscription_event=tuple(
            _deserialize_subscription_event(row) for row in tables["raw_subscription_event"]
        ),
        raw_base_snapshot=tuple(
            _deserialize_base_snapshot(row) for row in tables["raw_base_snapshot"]
        ),
        raw_order_book_snapshot=tuple(
            _deserialize_order_book_snapshot(row) for row in tables["raw_order_book_snapshot"]
        ),
    )


def _serialize_order_event(e: OrderEvent) -> dict[str, Any]:
    return {
        "node": e.node,
        "order_channel": e.order_channel,
        "txn_type": str(e.txn_type),
        "raise_period": str(e.raise_period),
        "event_type": e.event_type,
        "event_period": str(e.event_period),
        "count": str(e.count),
        "to_node": e.to_node,
    }


def _deserialize_order_event(row: dict[str, Any]) -> OrderEvent:
    return OrderEvent(
        node=NodeId(row["node"]),
        order_channel=ChannelId(row["order_channel"]),
        txn_type=TxnType(row["txn_type"]),
        raise_period=Period(int(row["raise_period"])),
        event_type=row["event_type"],
        event_period=Period(int(row["event_period"])),
        count=Decimal(row["count"]),
        to_node=NodeId(row["to_node"]) if row["to_node"] is not None else None,
    )


def _serialize_subscription_event(e: SubscriptionEvent) -> dict[str, Any]:
    return {
        "subscriber_id": e.subscriber_id,
        "event_type": e.event_type,
        "period": str(e.period),
        "node": e.node,
        "from_node": e.from_node,
        "acquisition_channel": e.acquisition_channel,
        "contract_term": e.contract_term,
        "tenure": e.tenure,
    }


def _deserialize_subscription_event(row: dict[str, Any]) -> SubscriptionEvent:
    return SubscriptionEvent(
        subscriber_id=row["subscriber_id"],
        event_type=row["event_type"],
        period=Period(int(row["period"])),
        node=NodeId(row["node"]),
        from_node=NodeId(row["from_node"]) if row["from_node"] is not None else None,
        acquisition_channel=ChannelId(row["acquisition_channel"]),
        contract_term=row["contract_term"],
        tenure=row["tenure"],
    )


def _serialize_base_snapshot(r: BaseSnapshot) -> dict[str, Any]:
    return {
        "node": r.node,
        "period": str(r.period),
        "opening_base": str(r.opening_base),
        "closing_base": str(r.closing_base),
        "closed_acquisition": str(r.closed_acquisition),
        "closed_resign_to": str(r.closed_resign_to),
        "closed_resign_from": str(r.closed_resign_from),
        "churn": str(r.churn),
        "migration_acq": str(r.migration_acq),
        "migration_churn": str(r.migration_churn),
    }


def _deserialize_base_snapshot(row: dict[str, Any]) -> BaseSnapshot:
    return BaseSnapshot(
        node=NodeId(row["node"]),
        period=Period(int(row["period"])),
        opening_base=Decimal(row["opening_base"]),
        closing_base=Decimal(row["closing_base"]),
        closed_acquisition=Decimal(row["closed_acquisition"]),
        closed_resign_to=Decimal(row["closed_resign_to"]),
        closed_resign_from=Decimal(row["closed_resign_from"]),
        churn=Decimal(row["churn"]),
        migration_acq=Decimal(row["migration_acq"]),
        migration_churn=Decimal(row["migration_churn"]),
    )


def _serialize_order_book_snapshot(r: OrderBookSnapshot) -> dict[str, Any]:
    return {
        "node": r.node,
        "order_channel": r.order_channel,
        "txn_type": str(r.txn_type),
        "period": str(r.period),
        "raised": str(r.raised),
        "closed": str(r.closed),
        "broken": str(r.broken),
        "open_orders": str(r.open_orders),
    }


def _deserialize_order_book_snapshot(row: dict[str, Any]) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        node=NodeId(row["node"]),
        order_channel=ChannelId(row["order_channel"]),
        txn_type=TxnType(row["txn_type"]),
        period=Period(int(row["period"])),
        raised=Decimal(row["raised"]),
        closed=Decimal(row["closed"]),
        broken=Decimal(row["broken"]),
        open_orders=Decimal(row["open_orders"]),
    )


# ---------------------------------------------------------------------------
# Dimension / ground-truth reporting tables -- the CLI's dim_product.json,
# dim_channel.json, and truth.json outputs.
# ---------------------------------------------------------------------------


def dim_product_rows(params: SyntheticParams) -> list[dict[str, Any]]:
    """One row per node: its product, product_group, and launch/retire
    period (None if it never launches late / never retires)."""
    ph = params.product_hierarchy
    return [
        {
            "node": node,
            "product": ph.product_of[node],
            "product_group": ph.group_of_product[ph.product_of[node]],
            "launch_period": params.node_launch_period.get(node),
            "retire_period": params.node_retire_period.get(node),
        }
        for node in ph.nodes
    ]


def dim_channel_rows(params: SyntheticParams) -> list[dict[str, Any]]:
    """One row per leaf channel: its channel_group and launch period."""
    ch = params.channel_hierarchy
    return [
        {
            "channel": channel,
            "channel_group": ch.group_of_channel[ch.channel_of[channel]],
            "launch_period": params.channel_launch_period.get(channel),
        }
        for channel in ch.sub_channels
    ]


def truth_summary(params: SyntheticParams) -> dict[str, Any]:
    """The subset of the spec's `output.truth` ground-truth artifacts that
    reduce cleanly to a static table: the closure kernel (g/breakage per
    channel x txn_type x product), the regrade transition matrix (including
    the EXTEND_SAME sentinel, written back out as the literal string
    "_extend_same"), regrade_extension_with_move_rate, and the migration
    schedule.

    Deliberately NOT included: channel_mix_by_node_period and
    acquisition_level_by_node_channel_period are period-varying series,
    already fully present (and more directly usable for parameter
    recovery) in raw_order_event/raw_subscription_event -- restating them
    here would just be a second, redundant copy. churn_hazard_by_tenure_
    term_channel / regrade_hazard_by_tenure_term aren't reducible to a
    static table under this module's contract-expiry-window hazard model
    (HazardFn is a function of period, tenure, months-since-contract-start,
    contract_term, acquisition_channel, and node -- not just tenure and
    term). amendment_matrix is Stage 2 (not implemented).
    """
    closure = {
        f"{channel}|{txn_type}|{product}": {
            "g": [str(v) for v in profile.g],
            "breakage": str(profile.breakage),
        }
        for (channel, txn_type, product), profile in params.closure.items()
    }
    regrade_transition = {
        str(source): {str(dest): str(p) for dest, p in dist.items()}
        for source, dist in params.regrade_transition.items()
    }
    migrations = [
        {
            "name": m.name,
            "from_node": m.from_node,
            "to_node": m.to_node,
            "schedule": {str(period): str(count) for period, count in m.schedule.items()},
        }
        for m in params.migrations
    ]
    return {
        "closure_kernel_g_k": closure,
        "regrade_transition_matrix": regrade_transition,
        "regrade_extension_with_move_rate": str(params.regrade_extension_with_move_rate),
        "migration_schedule": migrations,
    }
