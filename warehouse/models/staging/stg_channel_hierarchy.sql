-- Channel hierarchy dimension: sub_channel (order_channel) -> channel ->
-- channel_group.
select
    order_channel,
    channel,
    channel_group
from {{ ref('seed_channel_hierarchy') }}
