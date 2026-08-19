-- Identity 1 (order book), restated over actuals from the v2 event
-- pipeline: open_orders[t] = open_orders[t-1] + raised[t] - closed[t] -
-- broken[t]. Computed as a cumulative sum of net movement per node x
-- order_channel x txn_type, ordered by period -- see
-- tests/assert_agg_book_identity.sql for the independent verification.
with movements as (
    select
        node,
        order_channel,
        txn_type,
        case when event_type = 'raised' then raise_period else event_period end as period,
        sum(case when event_type = 'raised' then order_count else 0 end) as raised,
        sum(case when event_type = 'closed' then order_count else 0 end) as closed,
        sum(case when event_type = 'broken' then order_count else 0 end) as broken
    from {{ ref('stg_order_event') }}
    group by
        node,
        order_channel,
        txn_type,
        case when event_type = 'raised' then raise_period else event_period end
)

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
from movements
