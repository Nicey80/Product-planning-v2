select order_channel, channel, channel_group
from {{ ref('stg_channel_hierarchy') }}
