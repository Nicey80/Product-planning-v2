-- Product hierarchy dimension: variant (node) -> product -> product_group.
select
    node,
    product,
    product_group
from {{ ref('seed_node_hierarchy') }}
