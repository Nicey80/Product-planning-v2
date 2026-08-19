-- Drift between computed identities and source-system snapshots: residual
-- = snapshot - computed. Zero everywhere means the computed order book
-- and base tie out exactly to what the system of record reports; nonzero
-- rows point at a specific node/period/grain worth investigating. This is
-- the queryable counterpart to
-- tests/assert_agg_book_identity.sql/assert_agg_base_cohort_identity.sql,
-- which check internal consistency rather than agreement with an
-- external snapshot.
with book_residual as (
    select
        'book' as identity,
        s.node,
        s.order_channel,
        s.txn_type,
        s.period,
        s.open_orders_count as snapshot_value,
        b.open_orders as computed_value,
        s.open_orders_count - b.open_orders as residual
    from {{ ref('stg_order_book_snapshot') }} s
    left join {{ ref('agg_book') }} b
        on b.node = s.node
       and b.order_channel = s.order_channel
       and b.txn_type = s.txn_type
       and b.period = s.period
),

base_residual as (
    select
        'base' as identity,
        s.node,
        cast(null as string) as order_channel,
        cast(null as string) as txn_type,
        s.period,
        s.base_count as snapshot_value,
        c.closing_base as computed_value,
        s.base_count - c.closing_base as residual
    from {{ ref('stg_base_snapshot') }} s
    left join {{ ref('agg_base_cohort') }} c
        on c.node = s.node
       and c.period = s.period
)

select * from book_residual
union all
select * from base_residual
