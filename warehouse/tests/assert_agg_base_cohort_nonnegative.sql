-- Invariant 5 (base half), v2 event pipeline: base >= 0 at every node and
-- period. Passes (returns zero rows) when it holds everywhere.
select *
from {{ ref('agg_base_cohort') }}
where opening_base < 0 or closing_base < 0
