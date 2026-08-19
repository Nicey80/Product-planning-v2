-- Raised volume per node x order_channel x txn_type x period.
select
    node,
    order_channel,
    txn_type,
    raise_period as period,
    sum(order_count) as raised
from {{ ref('stg_order_event') }}
where event_type = 'raised'
group by node, order_channel, txn_type, raise_period
