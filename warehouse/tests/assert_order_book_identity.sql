-- Invariant 1: open_orders[t] = open_orders[t-1] + raised[t] - closed[t] - broken[t]
-- Recomputed independently via lag() (not the model's own window frame)
-- so this actually catches a broken fct_order_book, not just restates it.
-- Passes (returns zero rows) when the identity holds everywhere.
with book as (
    select
        node,
        order_channel,
        txn_type,
        period,
        raised,
        closed,
        broken,
        open_orders,
        lag(open_orders) over (
            partition by node, order_channel, txn_type order by period
        ) as prev_open_orders
    from {{ ref('fct_order_book') }}
)

select *
from book
where open_orders != coalesce(prev_open_orders, 0) + raised - closed - broken
