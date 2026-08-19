-- Source-system snapshot of open_orders per node x order_channel x
-- txn_type x period, independent of the roll-forward computed from
-- stg_order_event. Used by fact_reconciliation_residual to detect drift
-- between the computed order book (Identity 1) and the system of record.
select
    node,
    order_channel,
    txn_type,
    period,
    open_orders_count
from {{ ref('seed_order_book_snapshot') }}
