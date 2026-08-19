-- Closed volume per node x order_channel x txn_type x period (the period
-- the order actually closed in, not the raise period).
select
    node,
    order_channel,
    txn_type,
    event_period as period,
    sum(order_count) as closed
from {{ ref('stg_order_event') }}
where event_type = 'closed'
group by node, order_channel, txn_type, event_period
