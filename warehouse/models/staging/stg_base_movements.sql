-- Raw base-movement facts (closed transactions + migration overlays) per
-- node x period. Source of Identity 2. Never includes a raised/open_orders
-- column -- see CLAUDE.md invariant 7.
select
    period,
    node,
    closed_acquisition,
    closed_resign_to,
    closed_resign_from,
    churn,
    migration_acq,
    migration_churn
from {{ ref('seed_base_movements') }}
