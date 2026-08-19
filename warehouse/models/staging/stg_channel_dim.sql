-- Channel hierarchy dimension (sub_channel -> channel -> channel_group),
-- as reported by the source system. `sub_channel` is the leaf ChannelId
-- value used elsewhere as order_channel/acquisition_channel (see
-- CLAUDE.md glossary). Same underlying seed as stg_channel_hierarchy,
-- kept distinct for the v2 event/cohort pipeline.
select
    order_channel as sub_channel,
    channel,
    channel_group
from {{ ref('seed_channel_hierarchy') }}
