-- Raw closed-order-pipeline facts: raised, closed and broken counts per
-- node x order_channel x txn_type x period. Source of Identity 1.
select
    period,
    node,
    order_channel,
    txn_type,
    raised,
    closed,
    broken
from {{ ref('seed_orders') }}
