-- Empirical closure/breakage rates per raise cohort (node x order_channel
-- x txn_type x raise_period), computed from realized outcomes to date --
-- the historical counterpart of the closure kernel contract
-- (sum(g) + breakage == 1, CLAUDE.md invariant 2). This restates what
-- happened; estimating a forward-looking kernel from it is engine/'s job.
with resolutions as (
    select
        node,
        order_channel,
        txn_type,
        raise_period,
        sum(case when transition_type = 'closed' then count else 0 end) as total_closed,
        sum(case when transition_type = 'broken' then count else 0 end) as total_broken
    from {{ ref('int_transitions') }}
    group by node, order_channel, txn_type, raise_period
)

select
    ra.node,
    ra.order_channel,
    ra.txn_type,
    ra.period as raise_period,
    ra.raised,
    res.total_closed,
    res.total_broken,
    ra.raised - res.total_closed - res.total_broken as total_open,
    res.total_closed / nullif(ra.raised, 0) as closed_rate,
    res.total_broken / nullif(ra.raised, 0) as broken_rate,
    (ra.raised - res.total_closed - res.total_broken) / nullif(ra.raised, 0) as open_rate
from {{ ref('agg_raised') }} ra
inner join resolutions res
    on res.node = ra.node
   and res.order_channel = ra.order_channel
   and res.txn_type = ra.txn_type
   and res.raise_period = ra.period
