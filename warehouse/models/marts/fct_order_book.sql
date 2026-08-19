-- Identity 1 (order book), restated over actuals:
--   open_orders[t] = open_orders[t-1] + raised[t] - closed[t] - broken[t]
-- open_orders is a cumulative sum of net order-pipeline movement per
-- node x order_channel x txn_type, ordered by period -- computing it this
-- way (rather than forecasting it independently) is what makes the
-- identity hold exactly; tests/assert_order_book_identity.sql verifies
-- that independently via a lag()-based recomputation.
select
    node,
    order_channel,
    txn_type,
    period,
    raised,
    closed,
    broken,
    sum(raised - closed - broken) over (
        partition by node, order_channel, txn_type
        order by period
        rows between unbounded preceding and current row
    ) as open_orders
from {{ ref('stg_orders') }}
