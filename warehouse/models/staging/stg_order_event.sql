-- Event-level order-pipeline facts: one row per (raise cohort, resolution
-- period, event_type) observation. Unlike stg_orders (period-aggregated,
-- no cohort lineage), every row here carries both raise_period and
-- event_period so downstream models can compute age = event_period -
-- raise_period. Feeds int_order_cohort (the survival triangle) and
-- int_transitions.
select
    node,
    order_channel,
    txn_type,
    raise_period,
    event_period,
    event_type,
    order_count
from {{ ref('seed_order_event') }}
