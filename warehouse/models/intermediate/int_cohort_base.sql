-- Base-movement facts (Identity 2 inputs), aggregated from event-level
-- stg_subscription_event up to period grain. Mirrors stg_base_movements
-- but sourced from the v2 event pipeline instead of pre-aggregated seeds.
select
    node,
    period,
    sum(case when event_type = 'acquisition' then subscriber_count else 0 end) as closed_acquisition,
    sum(case when event_type = 'resign_to' then subscriber_count else 0 end) as closed_resign_to,
    sum(case when event_type = 'resign_from' then subscriber_count else 0 end) as closed_resign_from,
    sum(case when event_type = 'churn' then subscriber_count else 0 end) as churn,
    sum(case when event_type = 'migration_acq' then subscriber_count else 0 end) as migration_acq,
    sum(case when event_type = 'migration_churn' then subscriber_count else 0 end) as migration_churn
from {{ ref('stg_subscription_event') }}
group by node, period
