-- Product hierarchy dimension (variant/node -> product -> product_group),
-- as reported by the source system. Same underlying seed as
-- stg_node_hierarchy; kept as a distinct model so the v2 event/cohort
-- pipeline (int_*, agg_*) has its own clearly-named dimension reference
-- independent of the v1 stg_orders/fct_order_book pipeline.
select
    node,
    product,
    product_group
from {{ ref('seed_node_hierarchy') }}
