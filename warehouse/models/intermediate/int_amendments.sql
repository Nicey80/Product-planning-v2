-- Regrade amendments: node_raised (the resign_from source node) ->
-- node_closed (the resign_to destination node) for each regrade order,
-- paired by order_id from the same closed order in the same period (see
-- docs/domain-model.md section 5.1). Migration overlays never appear here
-- -- they have no order_id (invariant 8).
select
    f.order_id,
    f.period,
    f.node as node_raised,
    t.node as node_closed,
    f.subscriber_count as count
from {{ ref('stg_subscription_event') }} f
inner join {{ ref('stg_subscription_event') }} t
    on t.order_id = f.order_id
   and t.period = f.period
   and t.event_type = 'resign_to'
where f.event_type = 'resign_from'
