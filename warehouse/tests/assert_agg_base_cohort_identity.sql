-- Invariant 1 (Identity 2), v2 event pipeline: closing_base[t] =
-- opening_base[t] + net movement, and opening_base[t] = closing_base[t-1].
-- Recomputed independently via lag() so this catches a broken
-- agg_base_cohort, not just restates it. Passes (returns zero rows) when
-- the identity holds everywhere.
with base as (
    select
        node,
        period,
        opening_base,
        closing_base,
        closed_acquisition,
        closed_resign_to,
        closed_resign_from,
        churn,
        migration_acq,
        migration_churn,
        lag(closing_base) over (partition by node order by period) as prev_closing_base
    from {{ ref('agg_base_cohort') }}
)

select *
from base
where
    closing_base != opening_base
        + closed_acquisition + closed_resign_to - closed_resign_from
        - churn + migration_acq - migration_churn
    or (prev_closing_base is not null and opening_base != prev_closing_base)
