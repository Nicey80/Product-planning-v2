-- Each node's base as of the period immediately before the first period
-- tracked in stg_base_movements.
select
    node,
    opening_base
from {{ ref('seed_opening_base') }}
