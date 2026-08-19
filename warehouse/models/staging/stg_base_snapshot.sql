-- Source-system snapshot of closing base per node x period, independent of
-- the roll-forward computed from stg_subscription_event. Used by
-- fact_reconciliation_residual to detect drift between the computed base
-- (Identity 2) and what the system of record actually reports.
select
    node,
    period,
    base_count
from {{ ref('seed_base_snapshot') }}
