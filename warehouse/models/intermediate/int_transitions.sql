-- One row per raise-cohort resolution event: how many orders raised in
-- `raise_period` resolved (closed or broken) in `resolution_period`, and
-- at what lag. This is the raw raise-to-resolution lag distribution that
-- an empirical closure kernel g(k) would be estimated from (estimation
-- itself is engine/'s job, not warehouse/'s -- see CLAUDE.md "What
-- engine/ computes vs. what it doesn't").
select
    node,
    order_channel,
    txn_type,
    raise_period,
    event_period as resolution_period,
    event_period - raise_period as lag,
    event_type as transition_type,
    order_count as count
from {{ ref('stg_order_event') }}
where event_type in ('closed', 'broken')
