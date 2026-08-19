-- Event-level base-movement facts: one row per closed-transaction or
-- migration-overlay movement that affects a subscriber's base position.
-- `order_id` links the two legs of a regrade (resign_from/resign_to) so
-- int_amendments can pair them; it is null for migration_acq/migration_churn
-- rows, which never have an underlying order (invariant 8).
select
    order_id,
    node,
    channel,
    period,
    event_type,
    subscriber_count
from {{ ref('seed_subscription_event') }}
