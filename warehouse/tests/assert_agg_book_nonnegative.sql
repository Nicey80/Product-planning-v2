-- Invariant 5 (order book half), v2 event pipeline: open_orders >= 0 at
-- every node, channel and period. Passes (returns zero rows) when it
-- holds everywhere.
select *
from {{ ref('agg_book') }}
where open_orders < 0
