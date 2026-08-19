-- Reporting view of the survival triangle: each int_order_cohort cell
-- plus the raise cohort's original size, so consumers can compute
-- resolved/open share without a second join.
select
    c.raise_period,
    c.age,
    c.node,
    c.order_channel,
    c.txn_type,
    c.outcome,
    c.count,
    r.raised as cohort_size
from {{ ref('int_order_cohort') }} c
inner join {{ ref('agg_raised') }} r
    on r.node = c.node
   and r.order_channel = c.order_channel
   and r.txn_type = c.txn_type
   and r.period = c.raise_period
