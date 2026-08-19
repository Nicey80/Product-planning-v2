-- Invariant 3, v2 event pipeline: sum(closed_resign_to) ==
-- sum(closed_resign_from) across nodes, in each period. Passes (returns
-- zero rows) when regrades net to zero across the product hierarchy
-- every period.
select
    period,
    sum(closed_resign_to) as total_resign_to,
    sum(closed_resign_from) as total_resign_from
from {{ ref('agg_base_cohort') }}
group by period
having sum(closed_resign_to) != sum(closed_resign_from)
