select node, product, product_group
from {{ ref('stg_node_hierarchy') }}
