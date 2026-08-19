-- Identity 2 (base), restated over actuals:
--   closing_base[t] = opening_base[t]
--                    + closed_acquisition[t] + closed_resign_to[t]
--                    - closed_resign_from[t] - churn[t]
--                    + migration_acq[t] - migration_churn[t]
--   opening_base[t] = closing_base[t-1]
-- opening/closing base are cumulative sums of net movement per node,
-- seeded by stg_opening_base -- computed this way, not forecast
-- independently, so the identity holds exactly. Verified independently
-- via a lag()-based recomputation in tests/assert_base_identity.sql.
with movements as (
    select
        node,
        period,
        closed_acquisition,
        closed_resign_to,
        closed_resign_from,
        churn,
        migration_acq,
        migration_churn,
        (
            closed_acquisition + closed_resign_to - closed_resign_from
            - churn + migration_acq - migration_churn
        ) as net_movement
    from {{ ref('stg_base_movements') }}
)

select
    m.node,
    m.period,
    m.closed_acquisition,
    m.closed_resign_to,
    m.closed_resign_from,
    m.churn,
    m.migration_acq,
    m.migration_churn,
    o.opening_base + coalesce(
        sum(m.net_movement) over (
            partition by m.node order by m.period
            rows between unbounded preceding and 1 preceding
        ),
        0
    ) as opening_base,
    o.opening_base + sum(m.net_movement) over (
        partition by m.node order by m.period
        rows between unbounded preceding and current row
    ) as closing_base
from movements m
left join {{ ref('stg_opening_base') }} o on o.node = m.node
