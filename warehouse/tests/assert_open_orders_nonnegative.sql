-- Invariant 5 (order book half): open_orders >= 0 at every node, channel
-- and period. Passes (returns zero rows) when it holds everywhere.
select *
from {{ ref('fct_order_book') }}
where open_orders < 0
