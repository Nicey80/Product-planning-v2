-- Invariant 5 (base half): base >= 0 at every node and period. Passes
-- (returns zero rows) when it holds everywhere.
select *
from {{ ref('fct_base') }}
where opening_base < 0 or closing_base < 0
