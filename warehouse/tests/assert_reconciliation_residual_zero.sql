-- The computed Identity 1/Identity 2 marts must tie out exactly to the
-- source-system snapshots wherever a snapshot exists for that grain.
-- Passes (returns zero rows) when every residual is zero.
select *
from {{ ref('fact_reconciliation_residual') }}
where residual != 0
