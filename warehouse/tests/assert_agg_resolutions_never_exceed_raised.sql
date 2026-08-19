-- Invariant 4, v2 event pipeline: cumulative closed + cumulative broken +
-- open <= cumulative raised, per node x order_channel x txn_type. Passes
-- (returns zero rows) when it holds everywhere.
with cumulative as (
    select
        node,
        order_channel,
        txn_type,
        period,
        open_orders,
        sum(raised) over (
            partition by node, order_channel, txn_type
            order by period
            rows between unbounded preceding and current row
        ) as cum_raised,
        sum(closed) over (
            partition by node, order_channel, txn_type
            order by period
            rows between unbounded preceding and current row
        ) as cum_closed,
        sum(broken) over (
            partition by node, order_channel, txn_type
            order by period
            rows between unbounded preceding and current row
        ) as cum_broken
    from {{ ref('agg_book') }}
)

select *
from cumulative
where cum_closed + cum_broken + open_orders > cum_raised
